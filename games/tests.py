from django.contrib import admin
from django.test import SimpleTestCase

from games.admin import GameAdmin, GameWordAdmin, GuessAdmin, RoundAdmin
from games.models import Game, GameWord, Guess, Round


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
