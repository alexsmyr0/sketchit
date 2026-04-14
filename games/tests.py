import json
import threading
import time
from datetime import timedelta

import fakeredis
from django.contrib import admin
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from games import redis as game_redis
from games import runtime as game_runtime
from games.admin import GameAdmin, GameWordAdmin, GuessAdmin, RoundAdmin
from games.models import Game, GameStatus, GameWord, Guess, Round, RoundStatus
from games.services import (
    GuessEvaluationError,
    StartGameError,
    complete_round_due_to_timer,
    evaluate_guess_for_round,
    start_game_for_room,
)
from rooms.models import Player, Room
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


class StartGameServiceTests(TestCase):
    def setUp(self):
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

    def test_correct_guess_ends_active_round_and_updates_scores(self):
        first_round, guesser, drawer = self._start_game_with_non_drawer_guesser()
        guess_text = f"  {first_round.selected_game_word.text.upper()}  "

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
        self.assertIsNotNone(result.round_ended_at)
        self.assertEqual(first_round.status, RoundStatus.COMPLETED)
        self.assertIsNotNone(first_round.ended_at)
        self.assertEqual(guesser.current_score, 1)
        self.assertEqual(drawer.current_score, 1)
        self.assertEqual(self.spectator.current_score, 0)
        self.assertEqual(
            {(update.player_id, update.current_score) for update in result.score_updates},
            {(guesser.id, 1), (drawer.id, 1)},
        )
        self.assertEqual(result.as_round_result()["winning_player_id"], guesser.id)
        self.assertEqual(Guess.objects.filter(round=first_round).count(), 1)
        self.assertTrue(Guess.objects.get(round=first_round).is_correct)

    def test_completed_round_creates_next_round_with_unused_drawer_and_word(self):
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
        self.assertNotEqual(second_round.drawer_participant_id, self.spectator.id)
        self.assertNotEqual(second_round.selected_game_word_id, first_round.selected_game_word_id)

    def test_game_finishes_after_each_eligible_drawer_draws_once(self):
        started_game = start_game_for_room(self.room)
        game = started_game.game

        self._resolve_round_with_correct_guess(started_game.first_round)
        second_round = game.rounds.get(sequence_number=2)
        self._resolve_round_with_correct_guess(second_round)

        game.refresh_from_db()
        self.host.refresh_from_db()
        self.member.refresh_from_db()
        self.spectator.refresh_from_db()

        self.assertEqual(game.status, GameStatus.FINISHED)
        self.assertIsNotNone(game.ended_at)
        self.assertEqual(game.rounds.count(), 2)
        self.assertFalse(game.rounds.filter(sequence_number=3).exists())
        self.assertEqual(self.host.current_score, 2)
        self.assertEqual(self.member.current_score, 2)
        self.assertEqual(self.spectator.current_score, 0)

    def test_round_progression_never_repeats_drawers_or_words_within_game(self):
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
        self.assertIsNotNone(first_round.ended_at)
        self.assertEqual(guessers[0].current_score, 1)
        self.assertEqual(guessers[1].current_score, 1)
        self.assertEqual(drawer.current_score, 2)

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

        result = evaluate_guess_for_round(
            first_round,
            guessers[0],
            first_round.selected_game_word.text,
        )

        first_round.refresh_from_db()
        guessers[0].refresh_from_db()

        self.assertTrue(result.is_correct)
        self.assertFalse(result.round_completed)
        self.assertFalse(result.round_completed_now)
        self.assertIsNone(first_round.status)
        self.assertIsNone(first_round.ended_at)
        self.assertEqual(guessers[0].current_score, 1)

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
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.05,
    )
    def test_last_round_intermission_broadcasts_game_finished_and_clears_runtime_state(self):
        started_game = start_game_for_room(self.room)
        game = started_game.game
        first_round = started_game.first_round

        self._complete_round_with_all_guessers(first_round)

        def _second_round_exists() -> bool:
            return game.rounds.filter(sequence_number=2).exists()

        self._wait_for(_second_round_exists, timeout_seconds=5)
        second_round = game.rounds.get(sequence_number=2)
        self._complete_round_with_all_guessers(second_round)

        def _game_finished() -> bool:
            game.refresh_from_db()
            return game.status == GameStatus.FINISHED

        self._wait_for(_game_finished, timeout_seconds=6)

        self.assertIn("game.finished", self._event_types())
        self.assertEqual(game_redis.get_turn_state(self.fake_redis, self.room.join_code), {})
        self.assertIsNone(
            game_redis.get_deadline(self.fake_redis, self.room.join_code, "round_end")
        )
        self.assertIsNone(
            game_redis.get_deadline(
                self.fake_redis,
                self.room.join_code,
                "intermission_end",
            )
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

    def test_evaluate_guess_persists_guess_on_active_round(self):
        result = evaluate_guess_for_round(self.round, self.guesser, " rocket ")

        persisted_guess = Guess.objects.get(round=self.round, player=self.guesser)

        self.assertEqual(persisted_guess.text, " rocket ")
        self.assertTrue(persisted_guess.is_correct)
        self.assertEqual(result.guess.id, persisted_guess.id)
        self.assertEqual(result.guess.round_id, self.round.id)
        self.assertEqual(result.guess.player_id, self.guesser.id)

    def test_evaluate_guess_returns_correct_fields_for_n05_broadcasts(self):
        result = evaluate_guess_for_round(self.round, self.guesser, "rocket")

        # Verify N-05 broadcast payload requirements
        self.assertTrue(result.is_correct)
        self.assertTrue(result.round_completed)
        self.assertEqual(len(result.score_updates), 2)

        # Verify score_updates structure
        scores_by_player = {s.player_id: s.current_score for s in result.score_updates}
        self.assertEqual(scores_by_player[self.guesser.id], 1)
        self.assertEqual(scores_by_player[self.drawer.id], 1)

    def test_evaluate_guess_incorrect_payload(self):
        result = evaluate_guess_for_round(self.round, self.guesser, "wrong")

        self.assertFalse(result.is_correct)
        self.assertFalse(result.round_completed)
        self.assertEqual(len(result.score_updates), 0)
