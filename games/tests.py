import json
import threading
import time
from datetime import timedelta
from unittest.mock import patch

import fakeredis
from django.contrib import admin
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from redis.exceptions import RedisError

from games import redis as game_redis
from games import runtime as game_runtime
from games import services as game_services
from games.admin import GameAdmin, GameWordAdmin, GuessAdmin, RoundAdmin
from games.models import Game, GameStatus, GameWord, Guess, Round, RoundStatus
from games.services import (
    GuessEvaluationError,
    StartGameError,
    advance_game_after_intermission,
    build_game_leaderboard_snapshot,
    cancel_active_game_for_room,
    complete_leaderboard_cooldown_for_room,
    complete_round_due_to_timer,
    evaluate_guess_for_round,
    start_game_for_room,
)
from rooms.models import Player, Room
from rooms.services import leave_participant
from words.models import Word, WordPack, WordPackEntry


class GamesAdminRegistrationTests(SimpleTestCase):
    def test_game_domain_models_are_registered_in_admin(self):
        self.assertIsInstance(admin.site._registry.get(Game), GameAdmin)
        self.assertIsInstance(admin.site._registry.get(GameWord), GameWordAdmin)
        self.assertIsInstance(admin.site._registry.get(Round), RoundAdmin)
        self.assertIsInstance(admin.site._registry.get(Guess), GuessAdmin)

    def test_game_admin_configuration_matches_expected_setup(self):
        game_admin = admin.site._registry[Game]

        self.assertEqual(
            game_admin.list_display,
            ("id", "room", "status", "started_at", "ended_at", "created_at", "updated_at"),
        )
        self.assertEqual(game_admin.list_filter, ("status", "started_at", "created_at"))
        self.assertEqual(game_admin.search_fields, ("room__join_code", "room__name"))
        self.assertEqual(game_admin.raw_id_fields, ("room",))
        self.assertEqual(game_admin.readonly_fields, ("created_at", "updated_at"))
        self.assertEqual(game_admin.list_select_related, ("room",))

    def test_round_admin_configuration_matches_expected_setup(self):
        round_admin = admin.site._registry[Round]

        self.assertEqual(
            round_admin.search_fields,
            (
                "drawer_nickname",
                "game__room__join_code",
                "game__room__name",
                "selected_game_word__text",
            ),
        )
        self.assertEqual(round_admin.raw_id_fields, ("game", "drawer_participant", "selected_game_word"))
        self.assertEqual(round_admin.readonly_fields, ("created_at", "updated_at"))

    def test_guess_admin_configuration_matches_expected_setup(self):
        guess_admin = admin.site._registry[Guess]

        self.assertEqual(guess_admin.list_filter, ("typed_at", "created_at"))
        self.assertEqual(
            guess_admin.search_fields,
            (
                "text",
                "player__display_name",
                "player__room__join_code",
                "round__game__room__join_code",
            ),
        )
        self.assertEqual(guess_admin.raw_id_fields, ("round", "player"))


class RoomRuntimeTeardownTests(SimpleTestCase):
    """Verify room runtime teardown clears state and stops local workers."""

    def setUp(self):
        game_runtime.reset_runtime_state_for_tests()
        self.redis_client = fakeredis.FakeRedis()
        game_runtime._redis_client = self.redis_client

    def tearDown(self):
        game_runtime.reset_runtime_state_for_tests()
        super().tearDown()

    def test_teardown_room_runtime_stops_threads_and_clears_room_keys(self):
        join_code = "RUNT1234"
        stop_round = threading.Event()
        stop_intermission = threading.Event()
        round_thread = threading.Thread(
            target=stop_round.wait,
            args=(5,),
            daemon=True,
        )
        intermission_thread = threading.Thread(
            target=stop_intermission.wait,
            args=(5,),
            daemon=True,
        )
        round_thread.start()
        intermission_thread.start()

        with game_runtime._room_timer_lock:
            game_runtime._room_timer_handles_by_join_code[join_code] = (
                game_runtime._RoomTimerHandles(
                    active_round_id=5,
                    round_stop_event=stop_round,
                    round_thread=round_thread,
                    intermission_stop_event=stop_intermission,
                    intermission_thread=intermission_thread,
                )
            )

        game_redis.set_turn_state(
            self.redis_client,
            join_code,
            {
                "phase": "round",
                "status": "drawing",
                "game_id": "1",
                "round_id": "5",
                "deadline_at": "2030-01-01T00:00:00+00:00",
            },
        )
        game_redis.set_drawer_pool(self.redis_client, join_code, [1, 2, 3])
        game_redis.set_round_payloads(
            self.redis_client,
            join_code,
            {"word": "rocket"},
            {"mask": "r_____"},
        )
        game_redis.set_guess_state(
            self.redis_client,
            join_code,
            5,
            42,
            {"status": "correct"},
        )
        game_redis.set_deadline(
            self.redis_client,
            join_code,
            "round_end",
            "2030-01-01T00:00:00+00:00",
        )
        game_redis.set_deadline(
            self.redis_client,
            join_code,
            "intermission_end",
            "2030-01-01T00:00:05+00:00",
        )
        game_redis.set_deadline(
            self.redis_client,
            join_code,
            "cleanup",
            "2030-01-01T00:10:00+00:00",
        )

        game_runtime.teardown_room_runtime(
            join_code,
            include_cleanup_deadline=True,
        )

        self.assertTrue(stop_round.is_set())
        self.assertTrue(stop_intermission.is_set())
        self.assertFalse(round_thread.is_alive())
        self.assertFalse(intermission_thread.is_alive())
        self.assertEqual(
            game_runtime.get_timer_status_for_tests(join_code),
            {
                "round_timer_running": False,
                "intermission_timer_running": False,
                "drawer_disconnect_timer_running": False,
            },
        )
        self.assertEqual(game_redis.get_turn_state(self.redis_client, join_code), {})
        self.assertEqual(game_redis.get_drawer_pool(self.redis_client, join_code), set())
        self.assertIsNone(game_redis.get_round_payload(self.redis_client, join_code, "drawer"))
        self.assertIsNone(game_redis.get_guess_state(self.redis_client, join_code, 5, 42))
        self.assertIsNone(game_redis.get_deadline(self.redis_client, join_code, "round_end"))
        self.assertIsNone(
            game_redis.get_deadline(self.redis_client, join_code, "intermission_end")
        )
        self.assertIsNone(game_redis.get_deadline(self.redis_client, join_code, "cleanup"))


# The targeted test label ``games.tests.test_runtime_cleanup`` resolves against
# the ``games.tests`` module, so expose the teardown test class under that
# attribute name as well.
test_runtime_cleanup = RoomRuntimeTeardownTests


class StartGameServiceTests(TestCase):
    def setUp(self):
        self._original_services_redis_client = game_services._redis_client
        self.fake_redis = fakeredis.FakeRedis()
        game_services._redis_client = self.fake_redis

        self.word_pack = WordPack.objects.create(name="Test Pack")
        for word_text in ("apple", "banana", "cherry"):
            word = Word.objects.create(text=word_text)
            WordPackEntry.objects.create(word_pack=self.word_pack, word=word)

        self.room = Room.objects.create(
            name="Friday Sketches",
            join_code="ABCD1234",
            visibility=Room.Visibility.PRIVATE,
            status=Room.Status.LOBBY,
            word_pack=self.word_pack,
        )
        session_expires_at = timezone.now() + timedelta(hours=1)
        self.host = Player.objects.create(
            room=self.room,
            session_key="host-session",
            display_name="Host",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            current_score=50,
            session_expires_at=session_expires_at,
        )
        self.member = Player.objects.create(
            room=self.room,
            session_key="member-session",
            display_name="Member",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            current_score=30,
            session_expires_at=session_expires_at,
        )
        self.spectator = Player.objects.create(
            room=self.room,
            session_key="spectator-session",
            display_name="Spectator",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.SPECTATING,
            current_score=10,
            session_expires_at=session_expires_at,
        )
        self.room.host = self.host
        self.room.save(update_fields=("host",))

    def tearDown(self):
        game_services._redis_client = self._original_services_redis_client
        super().tearDown()

    def test_start_game_creates_snapshot_and_first_active_round(self):
        started_game = start_game_for_room(self.room)

        self.room.refresh_from_db()
        self.assertEqual(self.room.status, Room.Status.IN_PROGRESS)
        self.assertEqual(Game.objects.filter(room=self.room).count(), 1)

        game = started_game.game
        first_round = started_game.first_round
        snapshot_word_texts = list(
            game.snapshot_words.order_by("id").values_list("text", flat=True)
        )

        self.assertEqual(snapshot_word_texts, ["apple", "banana", "cherry"])
        self.assertEqual(first_round.game_id, game.id)
        self.assertEqual(first_round.sequence_number, 1)
        self.assertIsNone(first_round.status)
        self.assertIsNone(first_round.ended_at)
        self.assertIn(first_round.drawer_participant_id, {self.host.id, self.member.id})
        self.assertIn(first_round.selected_game_word.text, snapshot_word_texts)

        for participant in self.room.participants.all():
            self.assertEqual(participant.current_score, 0)

    def test_start_game_uses_game_word_snapshot_not_live_room_word_pack(self):
        started_game = start_game_for_room(self.room)
        game = started_game.game

        replacement_pack = WordPack.objects.create(name="Replacement Pack")
        replacement_word = Word.objects.create(text="replacement")
        WordPackEntry.objects.create(word_pack=replacement_pack, word=replacement_word)

        self.room.word_pack = replacement_pack
        self.room.save(update_fields=("word_pack",))
        self.word_pack.word_pack_entries.all().delete()

        self.assertEqual(
            list(game.snapshot_words.order_by("id").values_list("text", flat=True)),
            ["apple", "banana", "cherry"],
        )

    def test_start_game_requires_two_eligible_participants(self):
        self.member.participation_status = Player.ParticipationStatus.SPECTATING
        self.member.save(update_fields=("participation_status", "updated_at"))

        with self.assertRaises(StartGameError):
            start_game_for_room(self.room)

        self.assertEqual(Game.objects.filter(room=self.room).count(), 0)

    def test_start_game_requires_room_to_be_in_lobby(self):
        self.room.status = Room.Status.IN_PROGRESS
        self.room.save(update_fields=("status", "updated_at"))

        with self.assertRaises(StartGameError):
            start_game_for_room(self.room)

        self.assertEqual(Game.objects.filter(room=self.room).count(), 0)

    def test_start_game_treats_disconnected_participants_as_ineligible(self):
        self.member.connection_status = Player.ConnectionStatus.DISCONNECTED
        self.member.save(update_fields=("connection_status", "updated_at"))

        with self.assertRaises(StartGameError):
            start_game_for_room(self.room)

        self.assertEqual(Game.objects.filter(room=self.room).count(), 0)

    def test_start_game_requires_room_word_pack_to_have_words(self):
        empty_pack = WordPack.objects.create(name="Empty Pack")
        self.room.word_pack = empty_pack
        self.room.save(update_fields=("word_pack",))

        with self.assertRaisesMessage(
            StartGameError,
            "The room's selected word list has no words.",
        ):
            start_game_for_room(self.room)

        self.assertEqual(Game.objects.filter(room=self.room).count(), 0)

    def test_start_game_dedupes_case_variant_words_in_snapshot(self):
        case_variant_pack = WordPack.objects.create(name="Case Variants")
        lowercase_apple = Word.objects.create(text="apple")
        uppercase_apple = Word.objects.create(text="Apple")
        banana = Word.objects.create(text="banana")
        WordPackEntry.objects.create(word_pack=case_variant_pack, word=lowercase_apple)
        WordPackEntry.objects.create(word_pack=case_variant_pack, word=uppercase_apple)
        WordPackEntry.objects.create(word_pack=case_variant_pack, word=banana)

        self.room.word_pack = case_variant_pack
        self.room.save(update_fields=("word_pack", "updated_at"))

        started_game = start_game_for_room(self.room)

        self.assertEqual(
            list(started_game.game.snapshot_words.order_by("id").values_list("text", flat=True)),
            ["apple", "banana"],
        )

    def _start_game_with_non_drawer_guesser(self):
        started_game = start_game_for_room(self.room)
        first_round = started_game.first_round

        if first_round.drawer_participant_id == self.host.id:
            return first_round, self.member, self.host
        return first_round, self.host, self.member

    def _resolve_round_with_correct_guess(self, round_to_resolve: Round) -> None:
        guessers = list(
            Player.objects.filter(
                room=self.room,
                participation_status=Player.ParticipationStatus.PLAYING,
                created_at__lte=round_to_resolve.started_at,
            )
            .exclude(pk=round_to_resolve.drawer_participant_id)
            .order_by("created_at", "id")
        )
        self.assertTrue(guessers)

        for guesser in guessers:
            evaluate_guess_for_round(
                round_to_resolve,
                guesser,
                round_to_resolve.selected_game_word.text,
            )
            round_to_resolve.refresh_from_db()
            if round_to_resolve.status is not None:
                break

        self.assertEqual(round_to_resolve.status, RoundStatus.COMPLETED)

    def _finish_two_player_game(self) -> Game:
        """Finish a deterministic two-player game for A8 service tests.

        The default spectator from setUp would be promoted after round 1 and
        extend the game to a third round. These helper-driven A8 tests want the
        minimal two-drawer case, so we remove that incidental spectator first.
        """

        if Player.objects.filter(pk=self.spectator.id).exists():
            self.spectator.delete()

        started_game = start_game_for_room(self.room)
        game = started_game.game
        self._resolve_round_with_correct_guess(started_game.first_round)
        second_round = game.rounds.get(sequence_number=2)
        self._resolve_round_with_correct_guess(second_round)
        game.refresh_from_db()
        return game

    def _runtime_redis_client(self):
        return self.fake_redis

    def test_correct_guess_ends_active_round_and_updates_scores(self):
        first_round, guesser, drawer = self._start_game_with_non_drawer_guesser()
        guess_text = f"  {first_round.selected_game_word.text.upper()}  "

        round_start = timezone.now()
        first_round.started_at = round_start
        first_round.save(update_fields=("started_at", "updated_at"))
        accepted_at = round_start + timedelta(seconds=45)
        with patch("games.services.timezone.now", return_value=accepted_at):
            result = evaluate_guess_for_round(first_round, guesser, guess_text)

        first_round.refresh_from_db()
        guesser.refresh_from_db()
        drawer.refresh_from_db()
        self.spectator.refresh_from_db()

        self.assertTrue(result.is_correct)
        self.assertTrue(result.round_completed)
        self.assertTrue(result.round_completed_now)
        self.assertEqual(result.round_status, RoundStatus.COMPLETED)
        self.assertEqual(result.winning_player_id, guesser.id)
        self.assertEqual(result.round_ended_at, accepted_at)
        self.assertEqual(first_round.status, RoundStatus.COMPLETED)
        self.assertEqual(first_round.ended_at, accepted_at)
        self.assertEqual(guesser.current_score, 60)
        self.assertEqual(drawer.current_score, 30)
        self.assertEqual(self.spectator.current_score, 0)
        self.assertEqual(
            {(update.player_id, update.current_score) for update in result.score_updates},
            {
                (guesser.id, 60),
                (drawer.id, 30),
            },
        )
        self.assertEqual(result.as_round_result()["winning_player_id"], guesser.id)
        self.assertEqual(Guess.objects.filter(round=first_round).count(), 1)
        self.assertTrue(Guess.objects.get(round=first_round).is_correct)

    def test_start_game_initializes_remaining_drawer_pool_excluding_current_drawer(self):
        started_game = start_game_for_room(self.room)

        drawer_pool = game_redis.get_drawer_pool(
            self._runtime_redis_client(),
            self.room.join_code,
        )
        expected_remaining = {self.host.id, self.member.id}
        expected_remaining.discard(started_game.first_round.drawer_participant_id)

        self.assertSetEqual(drawer_pool, expected_remaining)

    def test_drawer_pool_updates_after_each_round_and_clears_on_game_finish(self):
        # A-07: the SPECTATING participant created in setUp would be promoted to
        # PLAYING at every round transition and would enter the drawer pool.
        # This test only cares about the three PLAYING drawers (host, member,
        # third_player), so drop the incidental spectator before starting the
        # game to keep the drawer-pool math deterministic.
        self.spectator.delete()
        third_player = Player.objects.create(
            room=self.room,
            session_key="third-drawer-pool-session",
            display_name="Third Pool",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            current_score=0,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )
        started_game = start_game_for_room(self.room)
        game = started_game.game
        redis_client = self._runtime_redis_client()
        all_drawer_ids = {self.host.id, self.member.id, third_player.id}

        first_round = started_game.first_round
        self._resolve_round_with_correct_guess(first_round)
        second_round = game.rounds.get(sequence_number=2)

        drawer_pool_after_second_round_created = game_redis.get_drawer_pool(
            redis_client,
            self.room.join_code,
        )
        self.assertSetEqual(
            drawer_pool_after_second_round_created,
            all_drawer_ids
            - {
                first_round.drawer_participant_id,
                second_round.drawer_participant_id,
            },
        )

        self._resolve_round_with_correct_guess(second_round)
        third_round = game.rounds.get(sequence_number=3)
        drawer_pool_after_third_round_created = game_redis.get_drawer_pool(
            redis_client,
            self.room.join_code,
        )
        self.assertSetEqual(drawer_pool_after_third_round_created, set())

        self._resolve_round_with_correct_guess(third_round)
        game.refresh_from_db()
        drawer_pool_after_game_finished = game_redis.get_drawer_pool(
            redis_client,
            self.room.join_code,
        )

        self.assertEqual(game.status, GameStatus.FINISHED)
        self.assertSetEqual(drawer_pool_after_game_finished, set())

    def test_completed_round_creates_next_round_with_unused_drawer_and_word(self):
        # A-07: the SPECTATING participant created in setUp would be promoted at
        # the round transition and become a valid drawer candidate, which would
        # make the "next drawer is host or member" assertion flaky. This test is
        # only about the unused-drawer/unused-word invariant for the two PLAYING
        # participants, so remove the incidental spectator first. Spectator
        # promotion semantics are covered by MidGameSpectatorRoundTransitionTests.
        spectator_id = self.spectator.id
        self.spectator.delete()

        started_game = start_game_for_room(self.room)
        game = started_game.game
        first_round = started_game.first_round

        self._resolve_round_with_correct_guess(first_round)

        game.refresh_from_db()
        first_round.refresh_from_db()
        second_round = game.rounds.select_related("selected_game_word").get(sequence_number=2)

        self.assertEqual(game.status, GameStatus.IN_PROGRESS)
        self.assertIsNone(game.ended_at)
        self.assertEqual(first_round.status, RoundStatus.COMPLETED)
        self.assertIsNone(second_round.status)
        self.assertIsNone(second_round.ended_at)
        self.assertNotEqual(second_round.drawer_participant_id, first_round.drawer_participant_id)
        self.assertIn(second_round.drawer_participant_id, {self.host.id, self.member.id})
        self.assertNotEqual(second_round.drawer_participant_id, spectator_id)
        self.assertNotEqual(second_round.selected_game_word_id, first_round.selected_game_word_id)

    def test_game_finishes_after_each_eligible_drawer_draws_once(self):
        # A-07: the SPECTATING participant created in setUp would be promoted at
        # the round-1 transition and add a third eligible drawer, so the game
        # would need three rounds to finish instead of two. This test is about
        # the two-PLAYING-drawer case, so drop the incidental spectator first.
        # Spectator promotion semantics are covered by
        # MidGameSpectatorRoundTransitionTests.
        self.spectator.delete()

        started_game = start_game_for_room(self.room)
        game = started_game.game

        self._resolve_round_with_correct_guess(started_game.first_round)
        second_round = game.rounds.get(sequence_number=2)
        self._resolve_round_with_correct_guess(second_round)

        game.refresh_from_db()
        self.host.refresh_from_db()
        self.member.refresh_from_db()

        self.assertEqual(game.status, GameStatus.FINISHED)
        self.assertIsNotNone(game.ended_at)
        self.assertEqual(game.rounds.count(), 2)
        self.assertFalse(game.rounds.filter(sequence_number=3).exists())
        selected_word_ids = list(
            game.rounds.order_by("sequence_number").values_list("selected_game_word_id", flat=True)
        )
        self.assertEqual(len(selected_word_ids), 2)
        self.assertEqual(len(selected_word_ids), len(set(selected_word_ids)))
        self.assertGreaterEqual(self.host.current_score, 30)
        self.assertLessEqual(self.host.current_score, 150)
        self.assertGreaterEqual(self.member.current_score, 30)
        self.assertLessEqual(self.member.current_score, 150)

    def test_build_game_leaderboard_snapshot_orders_entries_by_score_then_join_order(self):
        finished_game = Game.objects.create(
            room=self.room,
            status=GameStatus.FINISHED,
            ended_at=timezone.now(),
        )
        self.host.current_score = 25
        self.host.save(update_fields=("current_score", "updated_at"))
        self.member.current_score = 60
        self.member.save(update_fields=("current_score", "updated_at"))
        self.spectator.current_score = 60
        self.spectator.save(update_fields=("current_score", "updated_at"))

        snapshot = build_game_leaderboard_snapshot(finished_game.id)

        self.assertEqual(snapshot.game_id, finished_game.id)
        self.assertEqual(
            [entry.player_id for entry in snapshot.entries],
            [self.member.id, self.spectator.id, self.host.id],
        )
        self.assertEqual(
            snapshot.as_payload()["entries"][0],
            {
                "player_id": self.member.id,
                "display_name": self.member.display_name,
                "current_score": 60,
            },
        )

    def test_cancel_active_game_for_room_marks_game_and_active_round_cancelled(self):
        self.room.status = Room.Status.IN_PROGRESS
        self.room.save(update_fields=("status", "updated_at"))
        active_game = Game.objects.create(
            room=self.room,
            status=GameStatus.IN_PROGRESS,
        )
        game_word = GameWord.objects.create(game=active_game, text="rocket")
        active_round = Round.objects.create(
            game=active_game,
            drawer_participant=self.host,
            drawer_nickname=self.host.display_name,
            selected_game_word=game_word,
            sequence_number=1,
        )

        cancelled = cancel_active_game_for_room(self.room.id)

        active_game.refresh_from_db()
        active_round.refresh_from_db()
        self.assertTrue(cancelled)
        self.assertEqual(active_game.status, GameStatus.CANCELLED)
        self.assertIsNotNone(active_game.ended_at)
        self.assertEqual(active_round.status, RoundStatus.CANCELLED)
        self.assertIsNotNone(active_round.ended_at)
        self.assertFalse(cancel_active_game_for_room(self.room.id))

    @override_settings(SKETCHIT_ENABLE_RUNTIME_COORDINATOR=False)
    def test_complete_leaderboard_cooldown_restarts_game_with_fresh_scores(self):
        finished_game = self._finish_two_player_game()
        self.assertEqual(finished_game.status, GameStatus.FINISHED)

        result = complete_leaderboard_cooldown_for_room(self.room.id)

        self.room.refresh_from_db()
        self.host.refresh_from_db()
        self.member.refresh_from_db()

        self.assertTrue(result.restarted)
        self.assertEqual(result.room_status, Room.Status.IN_PROGRESS)
        self.assertIsNotNone(result.next_game_id)
        self.assertIsNotNone(result.next_round_id)
        self.assertEqual(self.room.status, Room.Status.IN_PROGRESS)
        self.assertEqual(Game.objects.filter(room=self.room).count(), 2)
        self.assertEqual(self.host.current_score, 0)
        self.assertEqual(self.member.current_score, 0)

    @override_settings(SKETCHIT_ENABLE_RUNTIME_COORDINATOR=False)
    def test_complete_leaderboard_cooldown_returns_room_to_lobby_when_too_few_players_remain(self):
        finished_game = self._finish_two_player_game()
        self.assertEqual(finished_game.status, GameStatus.FINISHED)
        self.member.connection_status = Player.ConnectionStatus.DISCONNECTED
        self.member.save(update_fields=("connection_status", "updated_at"))

        result = complete_leaderboard_cooldown_for_room(self.room.id)

        self.room.refresh_from_db()
        self.assertFalse(result.restarted)
        self.assertEqual(result.room_status, Room.Status.LOBBY)
        self.assertIsNone(result.next_game_id)
        self.assertIsNone(result.next_round_id)
        self.assertEqual(self.room.status, Room.Status.LOBBY)
        self.assertEqual(Game.objects.filter(room=self.room).count(), 1)

    @override_settings(SKETCHIT_ENABLE_RUNTIME_COORDINATOR=False)
    def test_complete_leaderboard_cooldown_promotes_connected_spectator_before_restart(self):
        finished_game = self._finish_two_player_game()
        self.assertEqual(finished_game.status, GameStatus.FINISHED)
        late_spectator = Player.objects.create(
            room=self.room,
            session_key="leaderboard-late-spectator",
            display_name="Late Spectator",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.SPECTATING,
            current_score=0,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )

        result = complete_leaderboard_cooldown_for_room(self.room.id)

        late_spectator.refresh_from_db()
        self.assertTrue(result.restarted)
        self.assertEqual(
            late_spectator.participation_status,
            Player.ParticipationStatus.PLAYING,
        )

    def test_round_progression_never_repeats_drawers_or_words_within_game(self):
        # A-07: the SPECTATING participant from setUp would be promoted at the
        # round-1 transition and become a fourth eligible drawer, so the first
        # three rounds would draw from a pool of four and one of the expected
        # drawers (host / member / third_player) might not appear in rounds
        # 1-3. This test asserts the round sequence for exactly three PLAYING
        # participants, so drop the incidental spectator first. A-07 promotion
        # semantics are covered by MidGameSpectatorRoundTransitionTests.
        self.spectator.delete()

        third_player = Player.objects.create(
            room=self.room,
            session_key="third-session",
            display_name="Third",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            current_score=0,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )
        started_game = start_game_for_room(self.room)
        game = started_game.game
        first_round = started_game.first_round

        self._resolve_round_with_correct_guess(first_round)
        second_round = game.rounds.get(sequence_number=2)
        self._resolve_round_with_correct_guess(second_round)
        third_round = game.rounds.get(sequence_number=3)

        drawer_ids = {
            first_round.drawer_participant_id,
            second_round.drawer_participant_id,
            third_round.drawer_participant_id,
        }
        self.assertSetEqual(drawer_ids, {self.host.id, self.member.id, third_player.id})

        selected_word_ids = list(
            game.rounds.order_by("sequence_number").values_list("selected_game_word_id", flat=True)
        )
        self.assertEqual(len(selected_word_ids), len(set(selected_word_ids)))

    def test_incorrect_guess_does_not_end_round_or_change_scores(self):
        first_round, guesser, _drawer = self._start_game_with_non_drawer_guesser()

        result = evaluate_guess_for_round(first_round, guesser, "definitely-not-the-word")

        first_round.refresh_from_db()
        self.host.refresh_from_db()
        self.member.refresh_from_db()
        self.spectator.refresh_from_db()

        self.assertFalse(result.is_correct)
        self.assertFalse(result.round_completed)
        self.assertFalse(result.round_completed_now)
        self.assertIsNone(result.round_status)
        self.assertIsNone(result.round_ended_at)
        self.assertIsNone(result.winning_player_id)
        self.assertEqual(result.score_updates, ())
        self.assertIsNone(first_round.status)
        self.assertIsNone(first_round.ended_at)
        self.assertEqual(self.host.current_score, 0)
        self.assertEqual(self.member.current_score, 0)
        self.assertEqual(self.spectator.current_score, 0)
        self.assertEqual(Guess.objects.filter(round=first_round).count(), 1)

    def test_late_guesses_do_not_change_completed_round_outcome_or_scores(self):
        first_round, first_winner, drawer = self._start_game_with_non_drawer_guesser()
        evaluate_guess_for_round(first_round, first_winner, first_round.selected_game_word.text)

        first_round.refresh_from_db()
        completed_at = first_round.ended_at
        first_winner.refresh_from_db()
        drawer.refresh_from_db()
        self.spectator.refresh_from_db()
        winner_score_after_resolution = first_winner.current_score
        drawer_score_after_resolution = drawer.current_score
        spectator_score_after_resolution = self.spectator.current_score

        result = evaluate_guess_for_round(
            first_round,
            drawer,
            first_round.selected_game_word.text,
        )

        first_round.refresh_from_db()
        first_winner.refresh_from_db()
        drawer.refresh_from_db()
        self.spectator.refresh_from_db()

        self.assertFalse(result.is_correct)
        self.assertTrue(result.round_completed)
        self.assertFalse(result.round_completed_now)
        self.assertEqual(result.round_status, RoundStatus.COMPLETED)
        self.assertEqual(result.round_ended_at, completed_at)
        self.assertEqual(result.winning_player_id, first_winner.id)
        self.assertEqual(result.score_updates, ())
        self.assertEqual(first_round.status, RoundStatus.COMPLETED)
        self.assertEqual(first_round.ended_at, completed_at)
        self.assertEqual(first_winner.current_score, winner_score_after_resolution)
        self.assertEqual(drawer.current_score, drawer_score_after_resolution)
        self.assertEqual(self.spectator.current_score, spectator_score_after_resolution)
        self.assertEqual(Guess.objects.filter(round=first_round).count(), 2)
        self.assertEqual(Guess.objects.filter(round=first_round, is_correct=True).count(), 1)

    def test_drawer_correct_guess_does_not_end_round(self):
        started_game = start_game_for_room(self.room)
        first_round = started_game.first_round
        drawer = first_round.drawer_participant

        result = evaluate_guess_for_round(first_round, drawer, first_round.selected_game_word.text)

        first_round.refresh_from_db()
        drawer.refresh_from_db()

        self.assertFalse(result.is_correct)
        self.assertFalse(result.round_completed)
        self.assertFalse(result.round_completed_now)
        self.assertIsNone(result.round_status)
        self.assertIsNone(result.round_ended_at)
        self.assertEqual(result.score_updates, ())
        self.assertIsNone(first_round.status)
        self.assertIsNone(first_round.ended_at)
        self.assertEqual(drawer.current_score, 0)
        self.assertEqual(Guess.objects.filter(round=first_round).count(), 1)

    def test_complete_round_due_to_timer_is_idempotent(self):
        started_game = start_game_for_room(self.room)
        first_round = started_game.first_round

        first_completion = complete_round_due_to_timer(first_round.id)
        first_round.refresh_from_db()
        first_ended_at = first_round.ended_at

        second_completion = complete_round_due_to_timer(first_round.id)
        first_round.refresh_from_db()

        self.assertTrue(first_completion)
        self.assertFalse(second_completion)
        self.assertEqual(first_round.status, RoundStatus.COMPLETED)
        self.assertEqual(first_round.ended_at, first_ended_at)

    def test_round_ends_only_after_all_eligible_non_drawers_are_correct(self):
        Player.objects.create(
            room=self.room,
            session_key="third-eligible-session",
            display_name="Third Eligible",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )
        started_game = start_game_for_room(self.room)
        first_round = started_game.first_round

        guessers = list(
            Player.objects.filter(
                room=self.room,
                participation_status=Player.ParticipationStatus.PLAYING,
                created_at__lte=first_round.started_at,
            )
            .exclude(pk=first_round.drawer_participant_id)
            .order_by("created_at", "id")
        )
        self.assertEqual(len(guessers), 2)

        round_start = timezone.now()
        first_round.started_at = round_start
        first_round.save(update_fields=("started_at", "updated_at"))

        first_guess_time = round_start + timedelta(seconds=10)
        with patch("games.services.timezone.now", return_value=first_guess_time):
            first_result = evaluate_guess_for_round(
                first_round,
                guessers[0],
                first_round.selected_game_word.text,
            )
        first_round.refresh_from_db()
        self.assertTrue(first_result.is_correct)
        self.assertFalse(first_result.round_completed_now)
        self.assertIsNone(first_round.status)
        self.assertIsNone(first_round.ended_at)

        second_guess_time = round_start + timedelta(seconds=70)
        with patch("games.services.timezone.now", return_value=second_guess_time):
            second_result = evaluate_guess_for_round(
                first_round,
                guessers[1],
                first_round.selected_game_word.text,
            )
        first_round.refresh_from_db()
        drawer = first_round.drawer_participant
        drawer.refresh_from_db()
        guessers[0].refresh_from_db()
        guessers[1].refresh_from_db()

        self.assertTrue(second_result.is_correct)
        self.assertTrue(second_result.round_completed)
        self.assertTrue(second_result.round_completed_now)
        self.assertEqual(first_round.status, RoundStatus.COMPLETED)
        self.assertEqual(first_round.ended_at, second_guess_time)
        self.assertEqual(guessers[0].current_score, 91)
        self.assertEqual(guessers[1].current_score, 38)
        self.assertEqual(drawer.current_score, 65)

    def test_disconnected_eligible_guesser_does_not_trigger_early_finish(self):
        Player.objects.create(
            room=self.room,
            session_key="third-disconnect-session",
            display_name="Third Disconnect",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )
        started_game = start_game_for_room(self.room)
        first_round = started_game.first_round

        guessers = list(
            Player.objects.filter(
                room=self.room,
                participation_status=Player.ParticipationStatus.PLAYING,
                created_at__lte=first_round.started_at,
            )
            .exclude(pk=first_round.drawer_participant_id)
            .order_by("created_at", "id")
        )
        self.assertEqual(len(guessers), 2)

        disconnected_guesser = guessers[1]
        disconnected_guesser.connection_status = Player.ConnectionStatus.DISCONNECTED
        disconnected_guesser.save(update_fields=("connection_status", "updated_at"))

        round_start = timezone.now()
        first_round.started_at = round_start
        first_round.save(update_fields=("started_at", "updated_at"))
        accepted_at = round_start + timedelta(seconds=45)
        with patch("games.services.timezone.now", return_value=accepted_at):
            result = evaluate_guess_for_round(
                first_round,
                guessers[0],
                first_round.selected_game_word.text,
            )

        first_round.refresh_from_db()
        drawer = first_round.drawer_participant
        drawer.refresh_from_db()
        guessers[0].refresh_from_db()

        self.assertTrue(result.is_correct)
        self.assertFalse(result.round_completed)
        self.assertFalse(result.round_completed_now)
        self.assertIsNone(first_round.status)
        self.assertIsNone(first_round.ended_at)
        self.assertEqual(guessers[0].current_score, 60)
        self.assertEqual(drawer.current_score, 30)

    @override_settings(
        SKETCHIT_ENABLE_RUNTIME_COORDINATOR=False,
        SKETCHIT_ROUND_DURATION_SECONDS=90,
    )
    def test_time_based_scoring_accumulates_for_multiple_guessers_at_different_times(self):
        Player.objects.create(
            room=self.room,
            session_key="third-time-score-session",
            display_name="Third Time Score",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )
        started_game = start_game_for_room(self.room)
        first_round = started_game.first_round
        drawer = first_round.drawer_participant

        guessers = list(
            Player.objects.filter(
                room=self.room,
                participation_status=Player.ParticipationStatus.PLAYING,
                created_at__lte=first_round.started_at,
            )
            .exclude(pk=first_round.drawer_participant_id)
            .order_by("created_at", "id")
        )
        self.assertEqual(len(guessers), 2)

        round_start = timezone.now()
        first_round.started_at = round_start
        first_round.save(update_fields=("started_at", "updated_at"))

        first_guess_time = round_start + timedelta(seconds=9)
        second_guess_time = round_start + timedelta(seconds=63)
        expected_first_guesser_points = 92
        expected_first_drawer_bonus = 46
        expected_second_guesser_points = 44
        expected_second_drawer_bonus = 22

        with patch("games.services.timezone.now", return_value=first_guess_time):
            first_result = evaluate_guess_for_round(
                first_round,
                guessers[0],
                first_round.selected_game_word.text,
            )

        first_round.refresh_from_db()
        guessers[0].refresh_from_db()
        drawer.refresh_from_db()

        self.assertTrue(first_result.is_correct)
        self.assertFalse(first_result.round_completed_now)
        self.assertIsNone(first_round.status)
        self.assertEqual(guessers[0].current_score, expected_first_guesser_points)
        self.assertEqual(drawer.current_score, expected_first_drawer_bonus)
        self.assertEqual(
            {(update.player_id, update.current_score) for update in first_result.score_updates},
            {
                (guessers[0].id, expected_first_guesser_points),
                (drawer.id, expected_first_drawer_bonus),
            },
        )

        with patch("games.services.timezone.now", return_value=second_guess_time):
            second_result = evaluate_guess_for_round(
                first_round,
                guessers[1],
                first_round.selected_game_word.text,
            )

        first_round.refresh_from_db()
        drawer.refresh_from_db()
        guessers[1].refresh_from_db()

        self.assertTrue(second_result.is_correct)
        self.assertTrue(second_result.round_completed_now)
        self.assertEqual(first_round.status, RoundStatus.COMPLETED)
        self.assertEqual(guessers[1].current_score, expected_second_guesser_points)
        self.assertEqual(
            drawer.current_score,
            expected_first_drawer_bonus + expected_second_drawer_bonus,
        )
        self.assertEqual(
            {(update.player_id, update.current_score) for update in second_result.score_updates},
            {
                (guessers[1].id, expected_second_guesser_points),
                (drawer.id, expected_first_drawer_bonus + expected_second_drawer_bonus),
            },
        )

    def test_evaluate_guess_rejects_spectating_participant(self):
        first_round, _guesser, _drawer = self._start_game_with_non_drawer_guesser()

        with self.assertRaisesMessage(
            GuessEvaluationError,
            "The guessing participant must be connected and have playing status.",
        ):
            evaluate_guess_for_round(first_round, self.spectator, first_round.selected_game_word.text)

        self.assertEqual(Guess.objects.filter(round=first_round).count(), 0)

    def test_evaluate_guess_rejects_disconnected_participant(self):
        first_round, guesser, _drawer = self._start_game_with_non_drawer_guesser()
        guesser.connection_status = Player.ConnectionStatus.DISCONNECTED
        guesser.save(update_fields=("connection_status", "updated_at"))

        with self.assertRaisesMessage(
            GuessEvaluationError,
            "The guessing participant must be connected and have playing status.",
        ):
            evaluate_guess_for_round(first_round, guesser, first_round.selected_game_word.text)

        self.assertEqual(Guess.objects.filter(round=first_round).count(), 0)

    def test_evaluate_guess_rejects_player_outside_round_room(self):
        first_round, _guesser, _drawer = self._start_game_with_non_drawer_guesser()
        outsider_room = Room.objects.create(
            name="Other Room",
            join_code="OTHER123",
            visibility=Room.Visibility.PUBLIC,
        )
        outsider = Player.objects.create(
            room=outsider_room,
            session_key="outsider-session",
            display_name="Outsider",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )

        with self.assertRaisesMessage(
            GuessEvaluationError,
            "The guessing participant must belong to the round's room.",
        ):
            evaluate_guess_for_round(first_round, outsider, first_round.selected_game_word.text)

        self.assertEqual(Guess.objects.filter(round=first_round).count(), 0)


@override_settings(SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True)
class SyncEventsForSpectatorTests(TestCase):
    """Verify that get_sync_events_for_player returns the correct event set
    for spectators (A-07).

    Spectators must receive round.state and round.timer so they can watch the
    live game, but must NOT receive round.started — that payload is role-
    specific and would mislead the client into thinking they can guess.
    """

    def setUp(self):
        super().setUp()
        game_runtime.reset_runtime_state_for_tests()
        self.fake_redis = fakeredis.FakeRedis()
        game_runtime._redis_client = self.fake_redis

        self.word_pack = WordPack.objects.create(name="Sync Pack")
        word = Word.objects.create(text="apple")
        WordPackEntry.objects.create(word_pack=self.word_pack, word=word)

        self.room = Room.objects.create(
            name="Sync Room",
            join_code="SYNC1234",
            visibility=Room.Visibility.PRIVATE,
            status=Room.Status.IN_PROGRESS,
            word_pack=self.word_pack,
        )
        session_expires_at = timezone.now() + timedelta(hours=1)

        self.drawer = Player.objects.create(
            room=self.room,
            session_key="drawer-session",
            display_name="Drawer",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=session_expires_at,
        )
        # Mid-game joiner — spectating the current turn.
        self.spectator = Player.objects.create(
            room=self.room,
            session_key="spectator-session",
            display_name="Spectator",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.SPECTATING,
            session_expires_at=session_expires_at,
        )
        self.guesser = Player.objects.create(
            room=self.room,
            session_key="guesser-session",
            display_name="Guesser",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=session_expires_at,
        )

        # Populate turn state so get_sync_events_for_player has something to work with.
        deadline_at = (timezone.now() + timedelta(seconds=60)).isoformat()
        game_redis.set_turn_state(
            self.fake_redis,
            self.room.join_code,
            {
                "phase": "round",
                "status": "drawing",
                "game_id": "1",
                "round_id": "1",
                "drawer_participant_id": str(self.drawer.id),
                "deadline_at": deadline_at,
                "eligible_guesser_ids": f"[{self.guesser.id}]",
                "correct_guesser_ids": "[]",
                "round_timer_sequence": "3",
                "intermission_timer_sequence": "0",
                "last_timer_server_timestamp": timezone.now().isoformat(),
            },
        )
        # Store the guesser payload so the guesser path has something to return.
        game_redis.set_round_payloads(
            self.fake_redis,
            self.room.join_code,
            drawer_payload={"role": "drawer", "word": "apple"},
            guesser_payload={"role": "guesser", "masked_word": "_____"},
        )

    def tearDown(self):
        game_runtime.reset_runtime_state_for_tests()
        super().tearDown()

    def _event_types(self, events: list[dict]) -> list[str]:
        """Return just the type strings from an event list for easy assertion."""
        return [e["type"] for e in events]

    def test_spectator_receives_round_state_and_timer_events(self):
        # A spectator must always get the phase snapshot so the client can
        # render the live game view (timer, drawer identity, etc.).
        events = game_runtime.get_sync_events_for_player(
            self.room.join_code, self.spectator.id
        )

        types = self._event_types(events)
        self.assertIn("round.state", types)
        self.assertIn("round.timer", types)

    def test_spectator_does_not_receive_round_started_event(self):
        # round.started carries the role-specific payload (masked word for
        # guessers, full word for the drawer). Spectators must not receive it
        # because it would wrongly suggest they can submit guesses.
        #
        # We also positively assert round.state and round.timer so this test
        # is self-defending: a regression that strips all round.* events for
        # spectators (e.g. an early empty return) would vacuously pass the
        # assertNotIn check. Asserting presence + absence in the same test
        # prevents that false-negative.
        events = game_runtime.get_sync_events_for_player(
            self.room.join_code, self.spectator.id
        )

        types = self._event_types(events)
        self.assertIn("round.state", types)
        self.assertIn("round.timer", types)
        self.assertNotIn("round.started", types)

    def test_spectator_does_not_receive_drawer_word_event(self):
        # Spectators must never receive the secret word — that would be
        # equivalent to giving them the answer during the active round.
        # Same self-defending pattern: assert the phase events are still
        # present so a "strip everything" regression can't sneak past.
        events = game_runtime.get_sync_events_for_player(
            self.room.join_code, self.spectator.id
        )

        types = self._event_types(events)
        self.assertIn("round.state", types)
        self.assertNotIn("round.drawer_word", types)

    def test_guesser_still_receives_round_started_event(self):
        # Sanity check: the spectator guard must not accidentally suppress
        # round.started for regular guessers.
        events = game_runtime.get_sync_events_for_player(
            self.room.join_code, self.guesser.id
        )

        types = self._event_types(events)
        self.assertIn("round.started", types)

    def test_drawer_still_receives_round_started_and_drawer_word_events(self):
        # Sanity check: the spectator guard must not affect the drawer path.
        events = game_runtime.get_sync_events_for_player(
            self.room.join_code, self.drawer.id
        )

        types = self._event_types(events)
        self.assertIn("round.started", types)
        self.assertIn("round.drawer_word", types)


@override_settings(SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True)
class RuntimeCoordinatorHelperTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        game_runtime.reset_runtime_state_for_tests()
        self.fake_redis = fakeredis.FakeRedis()
        game_runtime._redis_client = self.fake_redis

    def tearDown(self):
        game_runtime.reset_runtime_state_for_tests()
        super().tearDown()

    def test_mark_guesser_correct_returns_false_when_turn_state_is_missing(self):
        self.assertFalse(
            game_runtime.mark_guesser_correct(
                join_code="HELP1234",
                round_id=11,
                player_id=22,
            )
        )

    def test_mark_guesser_correct_returns_false_during_intermission_phase(self):
        game_redis.set_turn_state(
            self.fake_redis,
            "HELP1234",
            {
                "phase": "intermission",
                "round_id": "11",
                "eligible_guesser_ids": "[22]",
                "correct_guesser_ids": "[]",
            },
        )

        self.assertFalse(
            game_runtime.mark_guesser_correct(
                join_code="HELP1234",
                round_id=11,
                player_id=22,
            )
        )

    def test_mark_guesser_correct_returns_false_when_player_is_not_eligible(self):
        game_redis.set_turn_state(
            self.fake_redis,
            "HELP1234",
            {
                "phase": "round",
                "round_id": "11",
                "eligible_guesser_ids": "[22]",
                "correct_guesser_ids": "[]",
            },
        )

        self.assertFalse(
            game_runtime.mark_guesser_correct(
                join_code="HELP1234",
                round_id=11,
                player_id=99,
            )
        )
        state = game_redis.get_turn_state(self.fake_redis, "HELP1234")
        self.assertEqual(state.get("correct_guesser_ids"), "[]")

    def test_mark_guesser_correct_handles_concurrent_updates_without_corrupting_state(self):
        game_redis.set_turn_state(
            self.fake_redis,
            "HELP1234",
            {
                "phase": "round",
                "round_id": "11",
                "eligible_guesser_ids": "[22, 33]",
                "correct_guesser_ids": "[]",
            },
        )

        threads = [
            threading.Thread(
                target=game_runtime.mark_guesser_correct,
                kwargs={
                    "join_code": "HELP1234",
                    "round_id": 11,
                    "player_id": 22,
                },
            )
            for _ in range(6)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=1)

        updated_state = game_redis.get_turn_state(self.fake_redis, "HELP1234")
        self.assertEqual(
            set(json.loads(updated_state["correct_guesser_ids"])),
            {22},
        )


@override_settings(
    SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True,
    SKETCHIT_ROUND_DURATION_SECONDS=0.5,
    SKETCHIT_INTERMISSION_DURATION_SECONDS=2,
    SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.1,
)
class RoundTimerCoordinatorTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.word_pack = WordPack.objects.create(name="Timer Test Pack")
        for word_text in ("apple", "banana", "cherry"):
            word = Word.objects.create(text=word_text)
            WordPackEntry.objects.create(word_pack=self.word_pack, word=word)

        self.room = Room.objects.create(
            name="Timer Room",
            join_code="TIME1234",
            visibility=Room.Visibility.PRIVATE,
            status=Room.Status.LOBBY,
            word_pack=self.word_pack,
        )
        session_expires_at = timezone.now() + timedelta(hours=1)
        self.host = Player.objects.create(
            room=self.room,
            session_key="timer-host-session",
            display_name="Host",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=session_expires_at,
        )
        self.member = Player.objects.create(
            room=self.room,
            session_key="timer-member-session",
            display_name="Member",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=session_expires_at,
        )
        self.room.host = self.host
        self.room.save(update_fields=("host",))

        game_runtime.reset_runtime_state_for_tests()
        self.fake_redis = fakeredis.FakeRedis()
        game_runtime._redis_client = self.fake_redis

        self._event_log_lock = threading.Lock()
        self.event_log: list[tuple[str, dict]] = []
        self.original_room_broadcast = game_runtime.broadcast_room_event
        self.player_event_log: list[tuple[int, str, dict]] = []
        self.original_player_broadcast = game_runtime.broadcast_player_event

        def _capture_event(join_code: str, event_type: str, payload: dict) -> None:
            with self._event_log_lock:
                self.event_log.append((event_type, payload))

        def _capture_player_event(
            join_code: str,
            player_id: int,
            event_type: str,
            payload: dict,
        ) -> None:
            with self._event_log_lock:
                self.player_event_log.append((player_id, event_type, payload))

        game_runtime.broadcast_room_event = _capture_event
        game_runtime.broadcast_player_event = _capture_player_event

    def tearDown(self):
        game_runtime.broadcast_room_event = self.original_room_broadcast
        game_runtime.broadcast_player_event = self.original_player_broadcast
        game_runtime.reset_runtime_state_for_tests()
        super().tearDown()

    def _wait_for(self, predicate, timeout_seconds: float = 3.0) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        self.fail("Timed out waiting for expected runtime condition.")

    def _event_types(self) -> list[str]:
        with self._event_log_lock:
            return [event_type for event_type, _payload in self.event_log]

    def _event_payloads(self, event_type: str) -> list[dict]:
        with self._event_log_lock:
            return [
                payload
                for current_event_type, payload in self.event_log
                if current_event_type == event_type
            ]

    def _player_event_payloads(self, event_type: str) -> list[tuple[int, dict]]:
        with self._event_log_lock:
            return [
                (player_id, payload)
                for player_id, current_event_type, payload in self.player_event_log
                if current_event_type == event_type
            ]

    def _guessers_for_round(self, round: Round) -> list[Player]:
        return list(
            Player.objects.filter(
                room=self.room,
                participation_status=Player.ParticipationStatus.PLAYING,
                created_at__lte=round.started_at,
            )
            .exclude(pk=round.drawer_participant_id)
            .order_by("created_at", "id")
        )

    def _complete_round_with_all_guessers(self, round: Round) -> None:
        guessers = self._guessers_for_round(round)
        self.assertTrue(guessers)

        for guesser in guessers:
            evaluate_guess_for_round(round, guesser, round.selected_game_word.text)
            round.refresh_from_db()
            if round.status is not None:
                break

        self.assertEqual(round.status, RoundStatus.COMPLETED)

    def _finish_default_two_player_game(self) -> Game:
        """Run the default two-player room through its full first game cycle.

        The default fixture for this test class has exactly two PLAYING,
        CONNECTED participants. That means the first game should always finish
        after two completed rounds: one turn per eligible drawer.
        """

        started_game = start_game_for_room(self.room)
        game = started_game.game
        self._complete_round_with_all_guessers(started_game.first_round)

        def _second_round_exists() -> bool:
            return game.rounds.filter(sequence_number=2).exists()

        self._wait_for(_second_round_exists, timeout_seconds=5)
        second_round = game.rounds.get(sequence_number=2)
        self._complete_round_with_all_guessers(second_round)
        return game

    def test_server_timer_expires_round_and_starts_intermission(self):
        started_game = start_game_for_room(self.room)
        first_round = started_game.first_round

        def _round_completed() -> bool:
            first_round.refresh_from_db()
            return first_round.status == RoundStatus.COMPLETED

        self._wait_for(_round_completed, timeout_seconds=4)
        self.assertIsNotNone(first_round.ended_at)
        self.assertEqual(Guess.objects.filter(round=first_round).count(), 0)

        turn_state = game_redis.get_turn_state(self.fake_redis, self.room.join_code)
        self.assertEqual(turn_state.get("phase"), "intermission")
        self.assertEqual(turn_state.get("round_id"), str(first_round.id))

        self.assertIn("round.timer", self._event_types())
        self.assertIn("round.ended", self._event_types())
        self.assertIn("round.intermission_timer", self._event_types())

        round_timer_events = self._event_payloads("round.timer")
        self.assertTrue(round_timer_events)
        round_tick_sequences = [event["tick_sequence"] for event in round_timer_events]
        self.assertEqual(round_tick_sequences, sorted(round_tick_sequences))
        self.assertGreaterEqual(round_tick_sequences[0], 1)
        self.assertTrue(all("server_timestamp" in event for event in round_timer_events))

        intermission_timer_events = self._event_payloads("round.intermission_timer")
        self.assertTrue(intermission_timer_events)
        intermission_sequences = [
            event["tick_sequence"] for event in intermission_timer_events
        ]
        self.assertEqual(intermission_sequences, sorted(intermission_sequences))
        self.assertGreaterEqual(intermission_sequences[0], 1)
        self.assertTrue(
            all("server_timestamp" in event for event in intermission_timer_events)
        )

    def test_round_start_emits_drawer_only_word_event_hook(self):
        started_game = start_game_for_room(self.room)
        first_round = Round.objects.select_related("selected_game_word").get(
            pk=started_game.first_round.id
        )

        def _drawer_word_emitted() -> bool:
            return bool(self._player_event_payloads("round.drawer_word"))

        self._wait_for(_drawer_word_emitted, timeout_seconds=3)
        drawer_events = self._player_event_payloads("round.drawer_word")
        drawer_player_id, drawer_payload = drawer_events[-1]

        self.assertEqual(drawer_player_id, first_round.drawer_participant_id)
        self.assertEqual(drawer_payload["round_id"], first_round.id)
        self.assertEqual(drawer_payload["word"], first_round.selected_game_word.text)

    def test_round_start_stores_role_specific_payloads_without_word_leak_to_guessers(self):
        started_game = start_game_for_room(self.room)
        first_round = Round.objects.select_related("selected_game_word").get(
            pk=started_game.first_round.id
        )

        def _round_started_emitted() -> bool:
            return bool(self._event_payloads("round.started"))

        self._wait_for(_round_started_emitted, timeout_seconds=3)
        guesser_round_started_payload = self._event_payloads("round.started")[-1]

        drawer_payload = game_redis.get_round_payload(
            self.fake_redis,
            self.room.join_code,
            "drawer",
        )
        guesser_payload = game_redis.get_round_payload(
            self.fake_redis,
            self.room.join_code,
            "guesser",
        )

        self.assertIsNotNone(drawer_payload)
        self.assertIsNotNone(guesser_payload)
        self.assertEqual(drawer_payload["round_id"], first_round.id)
        self.assertEqual(guesser_payload["round_id"], first_round.id)
        self.assertEqual(drawer_payload["word"], first_round.selected_game_word.text)
        self.assertEqual(guesser_payload["masked_word"], drawer_payload["masked_word"])
        self.assertNotEqual(guesser_payload["masked_word"], first_round.selected_game_word.text)
        self.assertNotIn("word", guesser_payload)
        self.assertEqual(guesser_round_started_payload, guesser_payload)

    def test_runtime_sync_events_return_role_specific_round_started_payloads(self):
        started_game = start_game_for_room(self.room)
        first_round = Round.objects.select_related("selected_game_word").get(
            pk=started_game.first_round.id
        )

        def _round_payloads_ready() -> bool:
            return bool(
                game_redis.get_round_payload(
                    self.fake_redis,
                    self.room.join_code,
                    "drawer",
                )
            ) and bool(
                game_redis.get_round_payload(
                    self.fake_redis,
                    self.room.join_code,
                    "guesser",
                )
            )

        self._wait_for(_round_payloads_ready, timeout_seconds=3)

        drawer_id = first_round.drawer_participant_id
        guesser_id = self.member.id if drawer_id == self.host.id else self.host.id

        drawer_sync_events = game_runtime.get_sync_events_for_player(
            self.room.join_code,
            drawer_id,
        )
        guesser_sync_events = game_runtime.get_sync_events_for_player(
            self.room.join_code,
            guesser_id,
        )

        drawer_round_started = next(
            event for event in drawer_sync_events if event["type"] == "round.started"
        )
        guesser_round_started = next(
            event for event in guesser_sync_events if event["type"] == "round.started"
        )
        drawer_word_event = next(
            event for event in drawer_sync_events if event["type"] == "round.drawer_word"
        )

        self.assertEqual(drawer_round_started["payload"]["role"], "drawer")
        self.assertEqual(guesser_round_started["payload"]["role"], "guesser")
        self.assertEqual(
            drawer_round_started["payload"]["word"],
            first_round.selected_game_word.text,
        )
        self.assertNotIn("word", guesser_round_started["payload"])
        self.assertEqual(
            drawer_round_started["payload"]["masked_word"],
            guesser_round_started["payload"]["masked_word"],
        )
        self.assertEqual(drawer_word_event["payload"]["round_id"], first_round.id)
        self.assertEqual(
            drawer_word_event["payload"]["word"],
            first_round.selected_game_word.text,
        )

    def test_all_eligible_guessers_correct_ends_round_before_timer_expiry(self):
        Player.objects.create(
            room=self.room,
            session_key="timer-third-session",
            display_name="Third",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )
        started_game = start_game_for_room(self.room)
        first_round = started_game.first_round

        guessers = list(
            Player.objects.filter(
                room=self.room,
                participation_status=Player.ParticipationStatus.PLAYING,
                created_at__lte=first_round.started_at,
            )
            .exclude(pk=first_round.drawer_participant_id)
            .order_by("created_at", "id")
        )
        self.assertEqual(len(guessers), 2)

        first_result = evaluate_guess_for_round(
            first_round,
            guessers[0],
            first_round.selected_game_word.text,
        )
        first_round.refresh_from_db()
        self.assertTrue(first_result.is_correct)
        self.assertFalse(first_result.round_completed_now)
        self.assertIsNone(first_round.status)

        second_result = evaluate_guess_for_round(
            first_round,
            guessers[1],
            first_round.selected_game_word.text,
        )
        first_round.refresh_from_db()
        self.assertTrue(second_result.is_correct)
        self.assertTrue(second_result.round_completed_now)
        self.assertEqual(first_round.status, RoundStatus.COMPLETED)
        self.assertIsNotNone(first_round.ended_at)

        ended_events = [
            payload
            for event_type, payload in self.event_log
            if event_type == "round.ended"
        ]
        self.assertTrue(ended_events)
        self.assertEqual(ended_events[-1]["reason"], "all_guessers_correct")

    @override_settings(
        SKETCHIT_ROUND_DURATION_SECONDS=5,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=1,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.05,
        SKETCHIT_DRAWER_DISCONNECT_GRACE_SECONDS=0.25,
    )
    def test_drawer_disconnect_grace_expiry_ends_round_with_drawer_disconnected_status(self):
        started_game = start_game_for_room(self.room)
        first_round = started_game.first_round
        drawer_id = first_round.drawer_participant_id
        self.assertIsNotNone(drawer_id)

        game_runtime.handle_participant_disconnected(
            join_code=self.room.join_code,
            participant_id=drawer_id,
        )

        def _grace_state_published() -> bool:
            return any(
                payload.get("round_id") == first_round.id
                and payload.get("status") == "drawer_disconnected_grace"
                for payload in self._event_payloads("round.state")
            )

        self._wait_for(_grace_state_published, timeout_seconds=2)

        def _round_ended_with_drawer_disconnected() -> bool:
            first_round.refresh_from_db()
            return first_round.status == RoundStatus.DRAWER_DISCONNECTED

        self._wait_for(_round_ended_with_drawer_disconnected, timeout_seconds=3)

        ended_payloads = [
            payload
            for payload in self._event_payloads("round.ended")
            if payload.get("round_id") == first_round.id
        ]
        self.assertTrue(ended_payloads)
        self.assertEqual(ended_payloads[-1]["reason"], "drawer_disconnected")
        self.assertEqual(
            ended_payloads[-1]["status"],
            RoundStatus.DRAWER_DISCONNECTED,
        )

        turn_state = game_redis.get_turn_state(self.fake_redis, self.room.join_code)
        self.assertEqual(turn_state.get("phase"), "intermission")
        self.assertEqual(turn_state.get("round_id"), str(first_round.id))

    @override_settings(
        SKETCHIT_ROUND_DURATION_SECONDS=3,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=1,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.05,
        SKETCHIT_DRAWER_DISCONNECT_GRACE_SECONDS=0.4,
    )
    def test_drawer_reconnect_before_grace_deadline_keeps_round_active(self):
        started_game = start_game_for_room(self.room)
        first_round = started_game.first_round
        drawer_id = first_round.drawer_participant_id
        self.assertIsNotNone(drawer_id)

        game_runtime.handle_participant_disconnected(
            join_code=self.room.join_code,
            participant_id=drawer_id,
        )

        def _grace_started() -> bool:
            turn_state = game_redis.get_turn_state(self.fake_redis, self.room.join_code)
            return (
                turn_state.get("status") == "drawer_disconnected_grace"
                and bool(turn_state.get("drawer_disconnect_deadline_at"))
            )

        self._wait_for(_grace_started, timeout_seconds=2)

        game_runtime.handle_participant_reconnected(
            join_code=self.room.join_code,
            participant_id=drawer_id,
        )

        def _grace_cleared() -> bool:
            turn_state = game_redis.get_turn_state(self.fake_redis, self.room.join_code)
            return (
                turn_state.get("status") == "drawing"
                and not turn_state.get("drawer_disconnect_deadline_at")
            )

        self._wait_for(_grace_cleared, timeout_seconds=2)

        first_round.refresh_from_db()
        self.assertIsNone(first_round.status)
        self.assertIsNone(first_round.ended_at)

        ended_reasons = [
            payload["reason"]
            for payload in self._event_payloads("round.ended")
            if payload["round_id"] == first_round.id
        ]
        self.assertNotIn("drawer_disconnected", ended_reasons)

    @override_settings(
        SKETCHIT_ROUND_DURATION_SECONDS=5,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=1,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.05,
        SKETCHIT_DRAWER_DISCONNECT_GRACE_SECONDS=0.5,
    )
    def test_drawer_second_disconnect_in_same_round_starts_new_grace_window(self):
        started_game = start_game_for_room(self.room)
        first_round = started_game.first_round
        drawer_id = first_round.drawer_participant_id
        self.assertIsNotNone(drawer_id)

        game_runtime.handle_participant_disconnected(
            join_code=self.room.join_code,
            participant_id=drawer_id,
        )

        def _first_grace_started() -> bool:
            turn_state = game_redis.get_turn_state(self.fake_redis, self.room.join_code)
            return (
                turn_state.get("status") == "drawer_disconnected_grace"
                and bool(turn_state.get("drawer_disconnect_deadline_at"))
            )

        self._wait_for(_first_grace_started, timeout_seconds=2)
        first_deadline = game_redis.get_turn_state(
            self.fake_redis,
            self.room.join_code,
        ).get("drawer_disconnect_deadline_at")
        self.assertTrue(first_deadline)

        game_runtime.handle_participant_reconnected(
            join_code=self.room.join_code,
            participant_id=drawer_id,
        )

        def _first_grace_cleared() -> bool:
            turn_state = game_redis.get_turn_state(self.fake_redis, self.room.join_code)
            return (
                turn_state.get("status") == "drawing"
                and not turn_state.get("drawer_disconnect_deadline_at")
            )

        self._wait_for(_first_grace_cleared, timeout_seconds=2)

        game_runtime.handle_participant_disconnected(
            join_code=self.room.join_code,
            participant_id=drawer_id,
        )

        def _second_grace_started() -> bool:
            turn_state = game_redis.get_turn_state(self.fake_redis, self.room.join_code)
            return (
                turn_state.get("status") == "drawer_disconnected_grace"
                and bool(turn_state.get("drawer_disconnect_deadline_at"))
            )

        self._wait_for(_second_grace_started, timeout_seconds=2)
        second_deadline = game_redis.get_turn_state(
            self.fake_redis,
            self.room.join_code,
        ).get("drawer_disconnect_deadline_at")
        self.assertTrue(second_deadline)
        self.assertNotEqual(second_deadline, first_deadline)

        game_runtime.handle_participant_reconnected(
            join_code=self.room.join_code,
            participant_id=drawer_id,
        )
        self._wait_for(_first_grace_cleared, timeout_seconds=2)

        first_round.refresh_from_db()
        self.assertIsNone(first_round.status)
        self.assertIsNone(first_round.ended_at)

    @override_settings(
        SKETCHIT_ROUND_DURATION_SECONDS=5,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=1,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.05,
        SKETCHIT_DRAWER_DISCONNECT_GRACE_SECONDS=0.5,
    )
    def test_non_drawer_disconnect_during_drawer_grace_does_not_reset_or_end_round(self):
        started_game = start_game_for_room(self.room)
        first_round = started_game.first_round
        drawer_id = first_round.drawer_participant_id
        self.assertIsNotNone(drawer_id)
        non_drawer_id = self.member.id if drawer_id == self.host.id else self.host.id

        game_runtime.handle_participant_disconnected(
            join_code=self.room.join_code,
            participant_id=drawer_id,
        )

        def _grace_started() -> bool:
            turn_state = game_redis.get_turn_state(self.fake_redis, self.room.join_code)
            return (
                turn_state.get("status") == "drawer_disconnected_grace"
                and bool(turn_state.get("drawer_disconnect_deadline_at"))
            )

        self._wait_for(_grace_started, timeout_seconds=2)
        turn_state_before = game_redis.get_turn_state(self.fake_redis, self.room.join_code)
        grace_deadline_before = turn_state_before.get("drawer_disconnect_deadline_at")
        self.assertTrue(grace_deadline_before)

        game_runtime.handle_participant_disconnected(
            join_code=self.room.join_code,
            participant_id=non_drawer_id,
        )

        turn_state_after = game_redis.get_turn_state(self.fake_redis, self.room.join_code)
        self.assertEqual(turn_state_after.get("status"), "drawer_disconnected_grace")
        self.assertEqual(
            turn_state_after.get("drawer_disconnect_deadline_at"),
            grace_deadline_before,
        )

        game_runtime.handle_participant_reconnected(
            join_code=self.room.join_code,
            participant_id=drawer_id,
        )

        def _grace_cleared() -> bool:
            turn_state = game_redis.get_turn_state(self.fake_redis, self.room.join_code)
            return (
                turn_state.get("status") == "drawing"
                and not turn_state.get("drawer_disconnect_deadline_at")
            )

        self._wait_for(_grace_cleared, timeout_seconds=2)

        first_round.refresh_from_db()
        self.assertIsNone(first_round.status)
        self.assertIsNone(first_round.ended_at)

    @override_settings(
        SKETCHIT_ROUND_DURATION_SECONDS=5,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=1,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.05,
        SKETCHIT_DRAWER_DISCONNECT_GRACE_SECONDS=0.25,
    )
    def test_drawer_leaving_room_triggers_disconnect_grace_and_drawer_disconnected_outcome(self):
        started_game = start_game_for_room(self.room)
        first_round = started_game.first_round
        drawer_id = first_round.drawer_participant_id
        self.assertIsNotNone(drawer_id)

        leave_participant(
            redis_client=self.fake_redis,
            player_id=drawer_id,
        )

        def _round_ended_with_drawer_disconnected() -> bool:
            first_round.refresh_from_db()
            return first_round.status == RoundStatus.DRAWER_DISCONNECTED

        self._wait_for(_round_ended_with_drawer_disconnected, timeout_seconds=3)

        ended_payloads = [
            payload
            for payload in self._event_payloads("round.ended")
            if payload.get("round_id") == first_round.id
        ]
        self.assertTrue(ended_payloads)
        self.assertEqual(ended_payloads[-1]["reason"], "drawer_disconnected")
        self.assertEqual(
            ended_payloads[-1]["status"],
            RoundStatus.DRAWER_DISCONNECTED,
        )

    @override_settings(SKETCHIT_ROUND_DURATION_SECONDS=3)
    def test_redis_eligible_guesser_set_stays_stable_after_guesser_disconnect(self):
        Player.objects.create(
            room=self.room,
            session_key="timer-fourth-session",
            display_name="Fourth",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )
        started_game = start_game_for_room(self.room)
        first_round = started_game.first_round

        turn_state_before_disconnect = game_redis.get_turn_state(
            self.fake_redis,
            self.room.join_code,
        )
        eligible_before = set(json.loads(turn_state_before_disconnect["eligible_guesser_ids"]))

        guessers = self._guessers_for_round(first_round)
        disconnected_guesser = guessers[-1]
        disconnected_guesser.connection_status = Player.ConnectionStatus.DISCONNECTED
        disconnected_guesser.save(update_fields=("connection_status", "updated_at"))

        turn_state_after_disconnect = game_redis.get_turn_state(
            self.fake_redis,
            self.room.join_code,
        )
        eligible_after = set(json.loads(turn_state_after_disconnect["eligible_guesser_ids"]))

        self.assertEqual(eligible_before, eligible_after)
        self.assertIn(disconnected_guesser.id, eligible_after)

        result = evaluate_guess_for_round(
            first_round,
            guessers[0],
            first_round.selected_game_word.text,
        )
        first_round.refresh_from_db()

        self.assertTrue(result.is_correct)
        self.assertFalse(result.round_completed_now)
        self.assertIsNone(first_round.status)

    @override_settings(
        SKETCHIT_ROUND_DURATION_SECONDS=5,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=0.4,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.05,
    )
    def test_intermission_advances_to_next_round_automatically(self):
        started_game = start_game_for_room(self.room)
        game = started_game.game
        first_round = started_game.first_round

        self._complete_round_with_all_guessers(first_round)

        def _second_round_started() -> bool:
            return game.rounds.filter(sequence_number=2).exists()

        self._wait_for(_second_round_started, timeout_seconds=5)
        second_round = game.rounds.get(sequence_number=2)

        def _runtime_has_second_round_active() -> bool:
            turn_state = game_redis.get_turn_state(self.fake_redis, self.room.join_code)
            return (
                turn_state.get("phase") == "round"
                and turn_state.get("round_id") == str(second_round.id)
            )

        self._wait_for(_runtime_has_second_round_active, timeout_seconds=3)

        def _second_round_started_broadcasted() -> bool:
            started_round_ids = [
                payload["round_id"]
                for payload in self._event_payloads("round.started")
            ]
            return second_round.id in started_round_ids

        self._wait_for(_second_round_started_broadcasted, timeout_seconds=3)
        started_round_ids = [
            payload["round_id"]
            for payload in self._event_payloads("round.started")
        ]
        self.assertIn(first_round.id, started_round_ids)
        self.assertIn(second_round.id, started_round_ids)

    @override_settings(
        SKETCHIT_ROUND_DURATION_SECONDS=5,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=0.4,
        SKETCHIT_LEADERBOARD_DURATION_SECONDS=0.4,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.05,
    )
    def test_last_round_intermission_starts_leaderboard_cooldown_and_publishes_scoreboard_state(self):
        game = self._finish_default_two_player_game()

        def _scoreboard_state_emitted() -> bool:
            return bool(self._event_payloads("scoreboard.state"))

        self._wait_for(_scoreboard_state_emitted, timeout_seconds=6)

        def _game_finished() -> bool:
            game.refresh_from_db()
            return game.status == GameStatus.FINISHED

        self._wait_for(_game_finished, timeout_seconds=2)

        scoreboard_payload = self._event_payloads("scoreboard.state")[-1]
        turn_state = game_redis.get_turn_state(self.fake_redis, self.room.join_code)

        self.assertIn("game.finished", self._event_types())
        self.assertEqual(scoreboard_payload["game_id"], game.id)
        self.assertEqual(scoreboard_payload["phase"], "leaderboard")
        self.assertIn("deadline_at", scoreboard_payload)
        self.assertIn("remaining_seconds", scoreboard_payload)
        self.assertIn("entries", scoreboard_payload)
        self.assertEqual(turn_state.get("phase"), "leaderboard")
        self.assertEqual(turn_state.get("game_id"), str(game.id))
        # A8 changes the post-finish behavior: runtime stays alive during the
        # leaderboard cooldown instead of being torn down immediately.
        self.assertNotEqual(turn_state, {})

    @override_settings(
        SKETCHIT_ROUND_DURATION_SECONDS=5,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=0.4,
        SKETCHIT_LEADERBOARD_DURATION_SECONDS=0.8,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.05,
    )
    def test_leaderboard_sync_events_include_authoritative_scoreboard_snapshot(self):
        game = self._finish_default_two_player_game()

        def _scoreboard_state_emitted() -> bool:
            return bool(self._event_payloads("scoreboard.state"))

        self._wait_for(_scoreboard_state_emitted, timeout_seconds=6)

        # During the cooldown a reconnecting participant should get the same
        # authoritative leaderboard snapshot from runtime sync.
        sync_events = game_runtime.get_sync_events_for_player(
            self.room.join_code,
            self.host.id,
        )
        scoreboard_event = next(
            event for event in sync_events if event["type"] == "scoreboard.state"
        )

        self.assertEqual(scoreboard_event["payload"]["game_id"], game.id)
        self.assertEqual(scoreboard_event["payload"]["phase"], "leaderboard")
        self.assertIn("deadline_at", scoreboard_event["payload"])
        self.assertIn("remaining_seconds", scoreboard_event["payload"])
        self.assertIn("entries", scoreboard_event["payload"])

    @override_settings(
        SKETCHIT_ROUND_DURATION_SECONDS=5,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=0.4,
        SKETCHIT_LEADERBOARD_DURATION_SECONDS=0.4,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.05,
    )
    def test_leaderboard_cooldown_auto_starts_fresh_game_and_resets_scores(self):
        game = self._finish_default_two_player_game()

        def _scoreboard_state_emitted() -> bool:
            return bool(self._event_payloads("scoreboard.state"))

        self._wait_for(_scoreboard_state_emitted, timeout_seconds=6)

        self.host.refresh_from_db()
        self.member.refresh_from_db()
        self.assertGreater(self.host.current_score, 0)
        self.assertGreater(self.member.current_score, 0)

        def _second_game_started() -> bool:
            return Game.objects.filter(room=self.room).count() == 2

        self._wait_for(_second_game_started, timeout_seconds=4)

        second_game = Game.objects.filter(room=self.room).order_by("-id").first()
        self.assertIsNotNone(second_game)
        self.assertEqual(second_game.status, GameStatus.IN_PROGRESS)

        self.host.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual(self.host.current_score, 0)
        self.assertEqual(self.member.current_score, 0)
        self.assertNotEqual(second_game.id, game.id)

    @override_settings(
        SKETCHIT_ROUND_DURATION_SECONDS=5,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=0.4,
        SKETCHIT_LEADERBOARD_DURATION_SECONDS=0.4,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.05,
    )
    def test_leaderboard_cooldown_returns_room_to_lobby_when_too_few_players_remain(self):
        game = self._finish_default_two_player_game()

        def _scoreboard_state_emitted() -> bool:
            return bool(self._event_payloads("scoreboard.state"))

        self._wait_for(_scoreboard_state_emitted, timeout_seconds=6)

        # Only one eligible player remains once this participant is offline, so
        # A8 should fall back to the lobby instead of auto-starting a new game.
        self.member.connection_status = Player.ConnectionStatus.DISCONNECTED
        self.member.save(update_fields=("connection_status", "updated_at"))

        def _room_returned_to_lobby() -> bool:
            self.room.refresh_from_db()
            return self.room.status == Room.Status.LOBBY

        self._wait_for(_room_returned_to_lobby, timeout_seconds=4)

        game.refresh_from_db()
        self.assertEqual(game.status, GameStatus.FINISHED)
        self.assertEqual(Game.objects.filter(room=self.room).count(), 1)

    @override_settings(
        SKETCHIT_ROUND_DURATION_SECONDS=5,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=0.4,
        SKETCHIT_LEADERBOARD_DURATION_SECONDS=0.4,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.05,
    )
    def test_auto_restart_promotes_connected_spectators_before_next_game_starts(self):
        late_joiner = Player.objects.create(
            room=self.room,
            session_key="timer-late-joiner",
            display_name="Late Joiner",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.SPECTATING,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )

        self._finish_default_two_player_game()

        def _second_game_started() -> bool:
            return Game.objects.filter(room=self.room).count() == 2

        self._wait_for(_second_game_started, timeout_seconds=6)

        late_joiner.refresh_from_db()
        self.assertEqual(
            late_joiner.participation_status,
            Player.ParticipationStatus.PLAYING,
        )

    @override_settings(
        SKETCHIT_ROUND_DURATION_SECONDS=1.2,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=5,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.05,
    )
    def test_early_finish_cancels_round_timer_thread_before_deadline(self):
        Player.objects.create(
            room=self.room,
            session_key="timer-third-early-finish",
            display_name="Third Early",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )
        started_game = start_game_for_room(self.room)
        first_round = started_game.first_round

        self._complete_round_with_all_guessers(first_round)

        def _round_completed() -> bool:
            first_round.refresh_from_db()
            return first_round.status == RoundStatus.COMPLETED

        self._wait_for(_round_completed, timeout_seconds=2)
        time.sleep(0.2)
        timer_status = game_runtime.get_timer_status_for_tests(self.room.join_code)
        self.assertFalse(timer_status["round_timer_running"])

        time.sleep(1.3)
        ended_reasons = [
            payload["reason"]
            for payload in self._event_payloads("round.ended")
            if payload["round_id"] == first_round.id
        ]
        self.assertIn("all_guessers_correct", ended_reasons)
        self.assertNotIn("timer_expired", ended_reasons)


class GuessServiceIntegrationTests(TestCase):
    def setUp(self):
        self.word_pack = WordPack.objects.create(name="Guess Pack")
        self.word = Word.objects.create(text="rocket")
        self.second_word = Word.objects.create(text="planet")
        WordPackEntry.objects.create(word_pack=self.word_pack, word=self.word)
        WordPackEntry.objects.create(word_pack=self.word_pack, word=self.second_word)

        self.room = Room.objects.create(
            name="Guessing Room",
            join_code="GUESS999",
            status=Room.Status.IN_PROGRESS,
            word_pack=self.word_pack,
        )
        session_expires_at = timezone.now() + timedelta(hours=1)
        self.drawer = Player.objects.create(
            room=self.room, session_key="drawer-session", display_name="Drawer",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=session_expires_at,
        )
        self.guesser = Player.objects.create(
            room=self.room, session_key="guesser-session", display_name="Guesser",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=session_expires_at,
        )
        self.game = Game.objects.create(room=self.room, status=GameStatus.IN_PROGRESS)
        self.game_word = GameWord.objects.create(game=self.game, text="rocket")
        self.unused_game_word = GameWord.objects.create(game=self.game, text="planet")
        self.round = Round.objects.create(
            game=self.game,
            drawer_participant=self.drawer,
            drawer_nickname=self.drawer.display_name,
            selected_game_word=self.game_word,
            sequence_number=1,
        )

    def _set_round_start(self, started_at):
        self.round.started_at = started_at
        self.round.save(update_fields=("started_at", "updated_at"))

    def _score_map(self, result):
        return {update.player_id: update.current_score for update in result.score_updates}

    def test_evaluate_guess_persists_guess_on_active_round(self):
        result = evaluate_guess_for_round(self.round, self.guesser, " rocket ")

        persisted_guess = Guess.objects.get(round=self.round, player=self.guesser)

        self.assertEqual(persisted_guess.text, " rocket ")
        self.assertEqual(persisted_guess.normalized_text, "rocket")
        self.assertTrue(persisted_guess.is_correct)
        self.assertEqual(result.guess.id, persisted_guess.id)
        self.assertEqual(result.guess.round_id, self.round.id)
        self.assertEqual(result.guess.player_id, self.guesser.id)

    def test_evaluate_guess_returns_correct_fields_for_n05_broadcasts(self):
        round_start = timezone.now()
        self._set_round_start(round_start)
        accepted_at = round_start + timedelta(seconds=45)
        with patch("games.services.timezone.now", return_value=accepted_at):
            result = evaluate_guess_for_round(self.round, self.guesser, "rocket")

        # Verify N-05 broadcast payload requirements
        self.assertTrue(result.is_correct)
        self.assertEqual(result.outcome, game_services.GuessOutcome.CORRECT)
        self.assertTrue(result.round_completed)
        self.assertEqual(len(result.score_updates), 2)

        # Verify score_updates structure
        scores_by_player = self._score_map(result)
        self.assertEqual(scores_by_player[self.guesser.id], 60)
        self.assertEqual(scores_by_player[self.drawer.id], 30)

    @override_settings(SKETCHIT_ROUND_DURATION_SECONDS=90)
    def test_time_based_scoring_hits_maximum_boundary_at_round_start(self):
        round_start = timezone.now()
        self._set_round_start(round_start)

        with patch("games.services.timezone.now", return_value=round_start):
            result = evaluate_guess_for_round(self.round, self.guesser, "rocket")

        self.drawer.refresh_from_db()
        self.guesser.refresh_from_db()

        self.assertTrue(result.is_correct)
        self.assertEqual(self.guesser.current_score, 100)
        self.assertEqual(self.drawer.current_score, 50)
        self.assertEqual(
            self._score_map(result),
            {self.guesser.id: 100, self.drawer.id: 50},
        )

    @override_settings(SKETCHIT_ROUND_DURATION_SECONDS=90)
    def test_time_based_scoring_hits_minimum_boundary_at_deadline(self):
        round_start = timezone.now()
        self._set_round_start(round_start)
        accepted_at = round_start + timedelta(seconds=90)

        with patch("games.services.timezone.now", return_value=accepted_at):
            result = evaluate_guess_for_round(self.round, self.guesser, "rocket")

        self.drawer.refresh_from_db()
        self.guesser.refresh_from_db()

        self.assertTrue(result.is_correct)
        self.assertEqual(self.guesser.current_score, 20)
        self.assertEqual(self.drawer.current_score, 10)
        self.assertEqual(
            self._score_map(result),
            {self.guesser.id: 20, self.drawer.id: 10},
        )

    @override_settings(SKETCHIT_ROUND_DURATION_SECONDS=90)
    def test_time_based_scoring_clamps_to_minimum_after_deadline(self):
        round_start = timezone.now()
        self._set_round_start(round_start)
        accepted_at = round_start + timedelta(seconds=95)

        with patch("games.services.timezone.now", return_value=accepted_at):
            result = evaluate_guess_for_round(self.round, self.guesser, "rocket")

        self.drawer.refresh_from_db()
        self.guesser.refresh_from_db()

        self.assertTrue(result.is_correct)
        self.assertEqual(self.guesser.current_score, 20)
        self.assertEqual(self.drawer.current_score, 10)
        self.assertEqual(
            self._score_map(result),
            {self.guesser.id: 20, self.drawer.id: 10},
        )

    @override_settings(SKETCHIT_ROUND_DURATION_SECONDS=120)
    def test_time_based_scoring_uses_configured_round_duration(self):
        round_start = timezone.now()
        self._set_round_start(round_start)
        accepted_at = round_start + timedelta(seconds=30)

        with patch("games.services.timezone.now", return_value=accepted_at):
            result = evaluate_guess_for_round(self.round, self.guesser, "rocket")

        self.drawer.refresh_from_db()
        self.guesser.refresh_from_db()

        self.assertTrue(result.is_correct)
        self.assertEqual(self.guesser.current_score, 80)
        self.assertEqual(self.drawer.current_score, 40)
        self.assertEqual(
            self._score_map(result),
            {self.guesser.id: 80, self.drawer.id: 40},
        )

    @override_settings(
        SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True,
        SKETCHIT_ROUND_DURATION_SECONDS=90,
    )
    def test_runtime_turn_state_deadline_is_used_for_scoring(self):
        fake_redis = fakeredis.FakeRedis()
        accepted_at = timezone.now()
        self._set_round_start(accepted_at - timedelta(seconds=200))
        runtime_deadline = accepted_at + timedelta(seconds=45)
        game_redis.set_turn_state(
            fake_redis,
            self.room.join_code,
            {
                "phase": "round",
                "round_id": str(self.round.id),
                "eligible_guesser_ids": json.dumps([self.guesser.id]),
                "correct_guesser_ids": json.dumps([]),
            },
        )
        game_redis.update_turn_state_fields(
            fake_redis,
            self.room.join_code,
            {"deadline_at": runtime_deadline.isoformat()},
        )

        with patch("games.runtime.get_redis_client", return_value=fake_redis):
            with patch("games.services.timezone.now", return_value=accepted_at):
                result = evaluate_guess_for_round(self.round, self.guesser, "rocket")

        self.drawer.refresh_from_db()
        self.guesser.refresh_from_db()

        self.assertTrue(result.is_correct)
        self.assertEqual(self.guesser.current_score, 60)
        self.assertEqual(self.drawer.current_score, 30)
        self.assertEqual(
            self._score_map(result),
            {self.guesser.id: 60, self.drawer.id: 30},
        )

    @override_settings(SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True)
    def test_runtime_deadline_lookup_returns_none_on_redis_errors(self):
        for raised_error in (RedisError("redis unavailable"), OSError("socket failure")):
            with self.subTest(error_type=type(raised_error).__name__):
                with patch("games.runtime.get_redis_client", return_value=object()):
                    with patch(
                        "games.services.game_redis.get_turn_state",
                        side_effect=raised_error,
                    ):
                        self.assertIsNone(
                            game_services._runtime_round_deadline_for_scoring(self.round)
                        )

    @override_settings(
        SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True,
        SKETCHIT_ROUND_DURATION_SECONDS=90,
    )
    def test_runtime_deadline_lookup_failure_falls_back_to_started_at_formula(self):
        round_start = timezone.now()
        self._set_round_start(round_start)
        accepted_at = round_start + timedelta(seconds=45)

        with patch("games.runtime.get_redis_client", return_value=object()):
            with patch(
                "games.services.game_redis.get_turn_state",
                side_effect=RedisError("redis unavailable"),
            ):
                with patch("games.runtime.mark_guesser_correct", return_value=False):
                    with patch(
                        "games.runtime.get_round_correctness_state",
                        return_value=None,
                    ):
                        with patch("games.services.timezone.now", return_value=accepted_at):
                            result = evaluate_guess_for_round(self.round, self.guesser, "rocket")

        self.drawer.refresh_from_db()
        self.guesser.refresh_from_db()

        self.assertTrue(result.is_correct)
        self.assertEqual(self.guesser.current_score, 60)
        self.assertEqual(self.drawer.current_score, 30)
        self.assertEqual(
            self._score_map(result),
            {self.guesser.id: 60, self.drawer.id: 30},
        )

    def test_correct_guess_with_no_drawer_participant_awards_only_guesser(self):
        self.round.drawer_participant = None
        self.round.save(update_fields=("drawer_participant", "updated_at"))

        round_start = timezone.now()
        self._set_round_start(round_start)
        with patch("games.services.timezone.now", return_value=round_start):
            result = evaluate_guess_for_round(self.round, self.guesser, "rocket")

        self.drawer.refresh_from_db()
        self.guesser.refresh_from_db()

        self.assertTrue(result.is_correct)
        self.assertFalse(result.round_completed_now)
        self.assertEqual(self.guesser.current_score, 100)
        self.assertEqual(self.drawer.current_score, 0)
        self.assertEqual(self._score_map(result), {self.guesser.id: 100})

    def test_evaluate_guess_incorrect_payload(self):
        result = evaluate_guess_for_round(self.round, self.guesser, "wrong")

        self.assertFalse(result.is_correct)
        self.assertEqual(result.outcome, game_services.GuessOutcome.INCORRECT)
        self.assertFalse(result.round_completed)

    def test_duplicate_guess_is_same_player_only(self):
        session_expires_at = timezone.now() + timedelta(hours=1)
        other_guesser = Player.objects.create(
            room=self.room,
            session_key="other-guesser-session",
            display_name="Other Guesser",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=session_expires_at,
        )

        first_result = evaluate_guess_for_round(self.round, self.guesser, "planet")
        duplicate_result = evaluate_guess_for_round(self.round, self.guesser, "  PLANET  ")
        other_player_result = evaluate_guess_for_round(self.round, other_guesser, "planet")

        self.assertEqual(first_result.outcome, game_services.GuessOutcome.INCORRECT)
        self.assertEqual(duplicate_result.outcome, game_services.GuessOutcome.DUPLICATE)
        self.assertFalse(duplicate_result.is_correct)
        self.assertEqual(other_player_result.outcome, game_services.GuessOutcome.INCORRECT)

    def test_multi_word_target_single_token_guess_is_near_match(self):
        self.game_word.text = "new york city"
        self.game_word.save(update_fields=("text", "updated_at"))

        result = evaluate_guess_for_round(self.round, self.guesser, "york")

        self.assertFalse(result.is_correct)
        self.assertEqual(result.outcome, game_services.GuessOutcome.NEAR_MATCH)
        self.assertEqual(result.score_updates, ())

    def test_multi_word_exact_match_with_collapsed_whitespace_is_correct(self):
        self.game_word.text = "new york city"
        self.game_word.save(update_fields=("text", "updated_at"))

        result = evaluate_guess_for_round(self.round, self.guesser, "  NEW   york   city ")

        self.assertTrue(result.is_correct)
        self.assertEqual(result.outcome, game_services.GuessOutcome.CORRECT)

    def test_single_word_near_match_requires_strict_prefix_rule(self):
        near_match_result = evaluate_guess_for_round(self.round, self.guesser, "roc")
        short_prefix_result = evaluate_guess_for_round(self.round, self.guesser, "ro")

        self.assertEqual(near_match_result.outcome, game_services.GuessOutcome.NEAR_MATCH)
        self.assertEqual(short_prefix_result.outcome, game_services.GuessOutcome.INCORRECT)

    def test_single_word_near_match_does_not_use_loose_fuzzy_matching(self):
        result = evaluate_guess_for_round(self.round, self.guesser, "rcket")

        self.assertFalse(result.is_correct)
        self.assertEqual(result.outcome, game_services.GuessOutcome.INCORRECT)

    def test_casefold_handles_unicode_accents_for_correct_match(self):
        self.game_word.text = "café"
        self.game_word.save(update_fields=("text", "updated_at"))

        result = evaluate_guess_for_round(self.round, self.guesser, "CAFÉ")

        self.assertTrue(result.is_correct)
        self.assertEqual(result.outcome, game_services.GuessOutcome.CORRECT)

    def test_casefold_expansion_keeps_full_normalized_text_and_duplicate_detection(self):
        expanding_guess_text = "ß" * 255

        first_result = evaluate_guess_for_round(self.round, self.guesser, expanding_guess_text)
        second_result = evaluate_guess_for_round(self.round, self.guesser, expanding_guess_text)
        first_guess = Guess.objects.filter(round=self.round, player=self.guesser).earliest("id")

        self.assertEqual(first_result.outcome, game_services.GuessOutcome.INCORRECT)
        self.assertEqual(second_result.outcome, game_services.GuessOutcome.DUPLICATE)
        self.assertEqual(len(first_guess.normalized_text), 510)
        self.assertEqual(first_guess.normalized_text, "ss" * 255)

    def test_second_near_match_with_same_text_is_duplicate(self):
        self.game_word.text = "new york city"
        self.game_word.save(update_fields=("text", "updated_at"))

        first_result = evaluate_guess_for_round(self.round, self.guesser, "york")
        second_result = evaluate_guess_for_round(self.round, self.guesser, "  YORK ")

        self.assertEqual(first_result.outcome, game_services.GuessOutcome.NEAR_MATCH)
        self.assertEqual(second_result.outcome, game_services.GuessOutcome.DUPLICATE)

    def test_already_correct_repeat_same_text_is_duplicate_and_ignored(self):
        session_expires_at = timezone.now() + timedelta(hours=1)
        second_guesser = Player.objects.create(
            room=self.room,
            session_key="second-guesser-session",
            display_name="Second Guesser",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=session_expires_at,
        )
        round_start = timezone.now()
        self._set_round_start(round_start)

        with patch("games.services.timezone.now", return_value=round_start + timedelta(seconds=10)):
            first_result = evaluate_guess_for_round(self.round, self.guesser, "rocket")
        self.round.refresh_from_db()
        self.guesser.refresh_from_db()
        self.drawer.refresh_from_db()
        guesser_score_after_first_correct = self.guesser.current_score
        drawer_score_after_first_correct = self.drawer.current_score

        with patch("games.services.timezone.now", return_value=round_start + timedelta(seconds=20)):
            repeat_result = evaluate_guess_for_round(self.round, self.guesser, "  ROCKET ")
        self.round.refresh_from_db()
        self.guesser.refresh_from_db()
        self.drawer.refresh_from_db()

        self.assertEqual(first_result.outcome, game_services.GuessOutcome.CORRECT)
        self.assertFalse(first_result.round_completed_now)
        self.assertEqual(repeat_result.outcome, game_services.GuessOutcome.DUPLICATE)
        self.assertFalse(repeat_result.is_correct)
        self.assertFalse(repeat_result.round_completed)
        self.assertFalse(repeat_result.round_completed_now)
        self.assertIsNone(self.round.status)
        self.assertEqual(self.guesser.current_score, guesser_score_after_first_correct)
        self.assertEqual(self.drawer.current_score, drawer_score_after_first_correct)
        self.assertEqual(
            Guess.objects.filter(round=self.round, player=self.guesser, is_correct=True).count(),
            1,
        )

        with patch("games.services.timezone.now", return_value=round_start + timedelta(seconds=30)):
            second_result = evaluate_guess_for_round(self.round, second_guesser, "rocket")
        self.round.refresh_from_db()

        self.assertEqual(second_result.outcome, game_services.GuessOutcome.CORRECT)
        self.assertTrue(second_result.round_completed_now)
        self.assertEqual(self.round.status, RoundStatus.COMPLETED)

    def test_already_correct_then_near_match_is_incorrect_and_ignored(self):
        session_expires_at = timezone.now() + timedelta(hours=1)
        Player.objects.create(
            room=self.room,
            session_key="near-match-remaining-guesser-session",
            display_name="Remaining Guesser",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=session_expires_at,
        )
        round_start = timezone.now()
        self._set_round_start(round_start)

        with patch("games.services.timezone.now", return_value=round_start + timedelta(seconds=10)):
            first_result = evaluate_guess_for_round(self.round, self.guesser, "rocket")
        self.round.refresh_from_db()
        self.guesser.refresh_from_db()
        self.drawer.refresh_from_db()
        guesser_score_after_first_correct = self.guesser.current_score
        drawer_score_after_first_correct = self.drawer.current_score

        repeat_result = evaluate_guess_for_round(self.round, self.guesser, "roc")

        self.guesser.refresh_from_db()
        self.drawer.refresh_from_db()

        self.assertEqual(first_result.outcome, game_services.GuessOutcome.CORRECT)
        self.assertFalse(first_result.round_completed_now)
        self.assertEqual(repeat_result.outcome, game_services.GuessOutcome.INCORRECT)
        self.assertFalse(repeat_result.is_correct)
        self.assertFalse(repeat_result.round_completed_now)
        self.assertEqual(self.guesser.current_score, guesser_score_after_first_correct)
        self.assertEqual(self.drawer.current_score, drawer_score_after_first_correct)


@override_settings(SKETCHIT_ENABLE_RUNTIME_COORDINATOR=False)
class MidGameSpectatorRoundTransitionTests(TestCase):
    """Service-level coverage for A-07 spectator promotion at round transition.

    The runtime coordinator is disabled so advance_game_after_intermission
    drives round progression directly without needing real timers or Redis
    turn state. This lets us focus purely on the promotion and drawer-pool
    logic without threading complexity.
    """

    def setUp(self):
        self.word_pack = WordPack.objects.create(name="Transition Pack")
        for word_text in ("apple", "banana", "cherry"):
            word = Word.objects.create(text=word_text)
            WordPackEntry.objects.create(word_pack=self.word_pack, word=word)

        self.room = Room.objects.create(
            name="Transition Room",
            join_code="TRANS123",
            visibility=Room.Visibility.PRIVATE,
            status=Room.Status.IN_PROGRESS,
            word_pack=self.word_pack,
        )
        session_expires_at = timezone.now() + timedelta(hours=1)

        # The original drawer — will have already drawn in round 1.
        self.drawer = Player.objects.create(
            room=self.room,
            session_key="drawer-session",
            display_name="Drawer",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=session_expires_at,
        )
        # A regular participant who has not yet drawn.
        self.guesser = Player.objects.create(
            room=self.room,
            session_key="guesser-session",
            display_name="Guesser",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=session_expires_at,
        )
        # A mid-game joiner — currently spectating, eligible after promotion.
        self.spectator = Player.objects.create(
            room=self.room,
            session_key="spectator-session",
            display_name="Spectator",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.SPECTATING,
            session_expires_at=session_expires_at,
        )

        self.game = Game.objects.create(room=self.room, status=GameStatus.IN_PROGRESS)
        self.word_apple = GameWord.objects.create(game=self.game, text="apple")
        self.word_banana = GameWord.objects.create(game=self.game, text="banana")
        self.word_cherry = GameWord.objects.create(game=self.game, text="cherry")

        # Round 1 is already completed so advance_game_after_intermission can
        # move the game forward to round 2 when called in each test.
        self.completed_round = Round.objects.create(
            game=self.game,
            drawer_participant=self.drawer,
            drawer_nickname=self.drawer.display_name,
            selected_game_word=self.word_apple,
            sequence_number=1,
            status=RoundStatus.COMPLETED,
            ended_at=timezone.now(),
        )

    def test_spectator_is_promoted_to_playing_at_round_transition(self):
        # A-07: the spectator must become PLAYING when the round transitions
        # so they are a full participant from the next turn onward.
        advance_game_after_intermission(self.completed_round.id)

        self.spectator.refresh_from_db()
        self.assertEqual(
            self.spectator.participation_status,
            Player.ParticipationStatus.PLAYING,
        )

    def test_playing_participants_are_unchanged_at_round_transition(self):
        # Participants already marked PLAYING must not be touched by the
        # promotion step — their status was already correct.
        advance_game_after_intermission(self.completed_round.id)

        self.drawer.refresh_from_db()
        self.guesser.refresh_from_db()
        self.assertEqual(self.drawer.participation_status, Player.ParticipationStatus.PLAYING)
        self.assertEqual(self.guesser.participation_status, Player.ParticipationStatus.PLAYING)

    @patch("games.services.random.choice")
    def test_promoted_spectator_is_eligible_for_next_drawer_pool(self, mock_choice):
        # After promotion the spectator must be reachable by the drawer
        # selection. We force random.choice to pick the promoted spectator so
        # we can assert deterministically that they became the round 2 drawer.
        #
        # random.choice is called twice in _progress_game_after_round_completion:
        # once for the drawer and once for the word. We set a side_effect list
        # so the first call (drawer) returns the spectator and the second call
        # (word) returns a word to keep the round creation valid.
        mock_choice.side_effect = [self.spectator, self.word_banana]

        advance_game_after_intermission(self.completed_round.id)

        round_2 = Round.objects.filter(
            game=self.game, sequence_number=2
        ).first()

        self.assertIsNotNone(round_2)
        # The promoted spectator was selected as the drawer for round 2,
        # which proves they entered the eligible pool after promotion.
        self.assertEqual(round_2.drawer_participant_id, self.spectator.id)

    def test_round_transition_creates_next_round_after_promotion(self):
        # Sanity check: the game must progress to round 2 after promotion,
        # not stall because the drawer pool was evaluated before spectators
        # were promoted (which would incorrectly leave only one eligible drawer).
        advance_game_after_intermission(self.completed_round.id)

        self.assertEqual(
            Round.objects.filter(game=self.game).count(),
            2,
        )

    def test_round_transition_schedules_room_state_broadcast_when_promotion_occurs(self):
        # A-07 + A-06 integration: when the round transition promotes at least
        # one spectator, a fresh room.state broadcast must be scheduled so
        # clients update their lobby rendering. We patch the broadcast helper
        # at its import site inside games.services so we can observe the call
        # without touching the channel layer.
        with patch(
            "rooms.services.schedule_room_state_broadcast_after_commit",
        ) as mock_broadcast:
            advance_game_after_intermission(self.completed_round.id)

        mock_broadcast.assert_called_once_with(
            join_code=self.room.join_code,
            room_id=self.room.id,
        )

    def test_round_transition_skips_broadcast_when_no_spectators_to_promote(self):
        # Counterpart: if nobody needed to be promoted, the promotion helper
        # returns 0 and we must NOT schedule a redundant room.state broadcast.
        # Delete the lone spectator so the promotion finds no candidates.
        self.spectator.delete()

        with patch(
            "rooms.services.schedule_room_state_broadcast_after_commit",
        ) as mock_broadcast:
            advance_game_after_intermission(self.completed_round.id)

        mock_broadcast.assert_not_called()
