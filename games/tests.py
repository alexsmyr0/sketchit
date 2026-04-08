from datetime import timedelta

from django.contrib import admin
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from games.admin import GameAdmin, GameWordAdmin, GuessAdmin, RoundAdmin
from games.models import Game, GameWord, Guess, Round, RoundStatus
from games.services import (
    GuessEvaluationError,
    StartGameError,
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
