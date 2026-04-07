from datetime import timedelta

from django.contrib import admin
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from games.admin import GameAdmin, GameWordAdmin, GuessAdmin, RoundAdmin
from games.models import Game, GameWord, Guess, Round
from games.services import StartGameError, start_game_for_room
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

        with self.assertRaises(StartGameError):
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
