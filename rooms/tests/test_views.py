import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib import admin
from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from games.models import Game, GameWord, Round
from rooms.admin import PlayerAdmin, RoomAdmin
from rooms.models import MVP_DEFAULT_WORD_PACK_NAME, Player, Room
from words.models import Word, WordPack, WordPackEntry


class CreateRoomViewTests(TestCase):
    url = "/rooms/create/"

    def post_create_room(self, **overrides):
        payload = {
            "name": "Friday Sketches",
            "visibility": Room.Visibility.PRIVATE,
            "display_name": "Alex",
        }
        payload.update(overrides)
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_create_room_creates_lobby_room_and_host_player(self):
        response = self.post_create_room()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Room.objects.count(), 1)
        self.assertEqual(Player.objects.count(), 1)

        room = Room.objects.get()
        player = Player.objects.get()
        default_word_pack = WordPack.objects.get(name=MVP_DEFAULT_WORD_PACK_NAME)
        response_data = response.json()

        self.assertEqual(room.name, "Friday Sketches")
        self.assertEqual(room.visibility, Room.Visibility.PRIVATE)
        self.assertEqual(room.status, Room.Status.LOBBY)
        self.assertEqual(len(room.join_code), 8)
        self.assertEqual(room.host_id, player.id)
        self.assertEqual(room.word_pack_id, default_word_pack.id)

        self.assertEqual(player.room_id, room.id)
        self.assertEqual(player.display_name, "Alex")
        self.assertEqual(player.session_key, self.client.session.session_key)
        self.assertEqual(
            player.session_expires_at.replace(microsecond=0),
            self.client.session.get_expiry_date().replace(microsecond=0),
        )

        self.assertEqual(response_data["join_code"], room.join_code)
        self.assertIn(room.join_code, response_data["room_url"])

    def test_create_room_persists_session_for_guest_request(self):
        self.assertNotIn(settings.SESSION_COOKIE_NAME, self.client.cookies)

        response = self.post_create_room()

        self.assertEqual(response.status_code, 201)
        self.assertIn(settings.SESSION_COOKIE_NAME, self.client.cookies)

    def test_create_room_rejects_invalid_visibility_without_partial_data(self):
        response = self.post_create_room(visibility="friends_only")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Room.objects.count(), 0)
        self.assertEqual(Player.objects.count(), 0)
        self.assertIn("visibility", response.json()["errors"])

    def test_create_room_rejects_missing_name_without_partial_data(self):
        response = self.post_create_room(name="")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Room.objects.count(), 0)
        self.assertEqual(Player.objects.count(), 0)
        self.assertIn("name", response.json()["errors"])

    def test_create_room_rejects_missing_display_name_without_partial_data(self):
        response = self.post_create_room(display_name="")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Room.objects.count(), 0)
        self.assertEqual(Player.objects.count(), 0)
        self.assertIn("display_name", response.json()["errors"])

    def test_create_room_rejects_session_that_is_already_in_a_room(self):
        first_response = self.post_create_room()

        self.assertEqual(first_response.status_code, 201)

        response = self.post_create_room(name="Second Room")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(Room.objects.count(), 1)
        self.assertEqual(Player.objects.count(), 1)
        self.assertIn("detail", response.json())

    @patch(
        "rooms.views.generate_join_code",
        side_effect=["DUPLCODE", "UNIQCODE"],
        create=True,
    )
    def test_create_room_retries_when_generated_join_code_already_exists(
        self,
        _generate_join_code,
    ):
        Room.objects.create(
            name="Existing Room",
            join_code="DUPLCODE",
            visibility=Room.Visibility.PUBLIC,
        )

        response = self.post_create_room()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Room.objects.count(), 2)
        self.assertTrue(Room.objects.filter(join_code="UNIQCODE").exists())

    def test_create_room_uses_seeded_default_word_pack_even_when_other_packs_exist(self):
        WordPack.objects.create(name="Animals")
        expected_default_word_pack = WordPack.objects.get(name=MVP_DEFAULT_WORD_PACK_NAME)

        response = self.post_create_room()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Room.objects.get().word_pack_id, expected_default_word_pack.id)


class RoomWordPackModelTests(TestCase):
    def test_room_model_assigns_default_word_pack_when_not_provided(self):
        expected_default_word_pack = WordPack.objects.get(name=MVP_DEFAULT_WORD_PACK_NAME)

        room = Room.objects.create(
            name="Model Room",
            join_code="MODEL123",
            visibility=Room.Visibility.PRIVATE,
        )

        self.assertEqual(room.word_pack_id, expected_default_word_pack.id)


class JoinRoomViewTests(TestCase):
    def setUp(self):
        self.room = Room.objects.create(
            name="Friday Sketches",
            join_code="ABC12345",
            visibility=Room.Visibility.PRIVATE,
            max_players=3,
        )
        self.url = f"/rooms/{self.room.join_code}/join/"

    def post_join_room(self, client=None, join_code=None, content_type="application/json", raw_body=None, **overrides):
        payload = {"display_name": "Alex"}
        payload.update(overrides)
        client = client or self.client

        return client.post(
            f"/rooms/{join_code or self.room.join_code}/join/",
            data=json.dumps(payload) if raw_body is None else raw_body,
            content_type=content_type,
        )

    def test_join_room_creates_participant_for_existing_room(self):
        response = self.post_join_room()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Player.objects.count(), 1)

        player = Player.objects.get()
        response_data = response.json()

        self.assertEqual(player.room_id, self.room.id)
        self.assertEqual(player.display_name, "Alex")
        self.assertEqual(player.session_key, self.client.session.session_key)
        self.assertEqual(
            player.session_expires_at.replace(microsecond=0),
            self.client.session.get_expiry_date().replace(microsecond=0),
        )
        self.assertEqual(response_data["join_code"], self.room.join_code)
        self.assertEqual(response_data["room_url"], f"/rooms/{self.room.join_code}/")

    def test_join_room_persists_session_for_guest_request(self):
        self.assertNotIn(settings.SESSION_COOKIE_NAME, self.client.cookies)

        response = self.post_join_room()

        self.assertEqual(response.status_code, 201)
        self.assertIn(settings.SESSION_COOKIE_NAME, self.client.cookies)

    def test_join_room_reuses_existing_participant_for_same_session(self):
        first_response = self.post_join_room(display_name="Alex")

        player = Player.objects.get()

        session = self.client.session
        session.set_expiry(60 * 60 * 24 * 30)
        session.save()

        second_response = self.post_join_room(display_name="Changed Name")

        player.refresh_from_db()

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(Player.objects.count(), 1)
        self.assertEqual(player.display_name, "Alex")
        self.assertEqual(
            player.session_expires_at.replace(microsecond=0),
            self.client.session.get_expiry_date().replace(microsecond=0),
        )
        self.assertEqual(second_response.json()["join_code"], self.room.join_code)

    def test_join_room_allows_same_session_rejoin_even_when_room_is_full(self):
        self.room.max_players = 1
        self.room.save(update_fields=["max_players"])

        first_response = self.post_join_room()
        second_response = self.post_join_room(display_name="Changed Name")

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(Player.objects.count(), 1)

    def test_join_room_rejects_new_session_when_room_is_full(self):
        self.room.max_players = 1
        self.room.save(update_fields=["max_players"])
        self.post_join_room()

        other_client = self.client_class()

        response = self.post_join_room(client=other_client, display_name="Jamie")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(Player.objects.count(), 1)
        self.assertEqual(response.json()["detail"], "This room is full.")

    def test_join_room_rejects_session_already_assigned_to_another_room(self):
        other_room = Room.objects.create(
            name="Other Room",
            join_code="ZXCV5678",
            visibility=Room.Visibility.PUBLIC,
        )
        Player.objects.create(
            room=other_room,
            session_key="session-123",
            display_name="Alex",
            session_expires_at=self.client.session.get_expiry_date(),
        )

        session = self.client.session
        session.save()
        session["marker"] = "keep"
        session.save()

        Player.objects.filter(room=other_room).update(session_key=session.session_key)

        response = self.post_join_room()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(Player.objects.count(), 1)
        self.assertEqual(
            response.json()["detail"],
            "This guest session is already assigned to a room.",
        )

    def test_join_room_returns_404_for_unknown_join_code(self):
        response = self.post_join_room(join_code="missing1")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Player.objects.count(), 0)

    def test_join_room_normalizes_join_code_to_uppercase(self):
        response = self.post_join_room(join_code="abc12345")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Player.objects.get().room_id, self.room.id)

    def test_join_room_rejects_missing_display_name_without_partial_data(self):
        response = self.post_join_room(display_name="")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Player.objects.count(), 0)
        self.assertIn("display_name", response.json()["errors"])

    def test_join_room_rejects_invalid_json_without_partial_data(self):
        response = self.post_join_room(raw_body="{not json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Player.objects.count(), 0)
        self.assertIn("body", response.json()["errors"])

    def test_join_room_rejects_non_json_body_without_partial_data(self):
        response = self.post_join_room(content_type="text/plain", raw_body="display_name=Alex")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Player.objects.count(), 0)
        self.assertIn("body", response.json()["errors"])

    def test_join_room_rejects_non_post_requests(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)
        self.assertEqual(Player.objects.count(), 0)


class RoomLobbyStateViewTests(TestCase):
    def _ensure_session_key(self, client):
        session = client.session
        session.save()
        return session.session_key

    def setUp(self):
        self.room = Room.objects.create(
            name="Friday Sketches",
            join_code="ABC12345",
            visibility=Room.Visibility.PUBLIC,
            status=Room.Status.LOBBY,
        )
        self.url = f"/rooms/{self.room.join_code}/"

        self.host_client = self.client_class()
        host_session_key = self._ensure_session_key(self.host_client)
        self.host_player = Player.objects.create(
            room=self.room,
            session_key=host_session_key,
            display_name="Host Alex",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=self.host_client.session.get_expiry_date(),
        )

        self.member_client = self.client_class()
        member_session_key = self._ensure_session_key(self.member_client)
        self.member_player = Player.objects.create(
            room=self.room,
            session_key=member_session_key,
            display_name="Jamie",
            connection_status=Player.ConnectionStatus.DISCONNECTED,
            participation_status=Player.ParticipationStatus.SPECTATING,
            session_expires_at=self.member_client.session.get_expiry_date(),
        )

        self.room.host = self.host_player
        self.room.save(update_fields=["host"])

    def test_member_can_load_room_lobby_state(self):
        response = self.member_client.get(self.url)

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(
            data["room"],
            {
                "name": self.room.name,
                "join_code": self.room.join_code,
                "visibility": self.room.visibility,
                "status": self.room.status,
            },
        )
        self.assertEqual(
            data["host"],
            {
                "id": self.host_player.id,
                "display_name": self.host_player.display_name,
            },
        )
        self.assertEqual(len(data["participants"]), 2)
        self.assertSetEqual(
            set(data["participants"][0].keys()),
            {"id", "display_name", "connection_status", "participation_status"},
        )

        participants_by_id = {
            participant["id"]: participant for participant in data["participants"]
        }
        self.assertEqual(
            participants_by_id[self.host_player.id],
            {
                "id": self.host_player.id,
                "display_name": self.host_player.display_name,
                "connection_status": self.host_player.connection_status,
                "participation_status": self.host_player.participation_status,
            },
        )
        self.assertEqual(
            participants_by_id[self.member_player.id],
            {
                "id": self.member_player.id,
                "display_name": self.member_player.display_name,
                "connection_status": self.member_player.connection_status,
                "participation_status": self.member_player.participation_status,
            },
        )

    def test_non_member_session_cannot_load_room_lobby_state(self):
        outsider_client = self.client_class()
        outsider_session_key = self._ensure_session_key(outsider_client)

        other_room = Room.objects.create(
            name="Other Room",
            join_code="ZXCV5678",
            visibility=Room.Visibility.PRIVATE,
        )
        Player.objects.create(
            room=other_room,
            session_key=outsider_session_key,
            display_name="Outsider",
            session_expires_at=outsider_client.session.get_expiry_date(),
        )

        response = outsider_client.get(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"],
            "This guest session is not a participant in this room.",
        )

    def test_room_lobby_state_returns_status_as_stored(self):
        self.room.status = Room.Status.IN_PROGRESS
        self.room.save(update_fields=["status"])

        response = self.member_client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["room"]["status"], Room.Status.IN_PROGRESS)

    def test_room_lobby_state_normalizes_join_code_to_uppercase(self):
        response = self.member_client.get("/rooms/abc12345/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["room"]["join_code"], self.room.join_code)

    def test_room_lobby_state_returns_404_for_unknown_join_code(self):
        response = self.member_client.get("/rooms/missing1/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Room not found.")

    def test_room_lobby_state_rejects_non_get_requests(self):
        response = self.member_client.post(
            self.url,
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 405)


class StartGameViewTests(TestCase):
    def _ensure_session_key(self, client):
        session = client.session
        session.save()
        return session.session_key

    def setUp(self):
        self.word_pack = WordPack.objects.create(name="Room Pack")
        for word_text in ("apple", "banana", "cherry"):
            word = Word.objects.create(text=word_text)
            WordPackEntry.objects.create(word_pack=self.word_pack, word=word)

        self.room = Room.objects.create(
            name="Friday Sketches",
            join_code="ABC12345",
            visibility=Room.Visibility.PRIVATE,
            status=Room.Status.LOBBY,
            word_pack=self.word_pack,
        )
        self.url = f"/rooms/{self.room.join_code}/start-game/"
        session_expires_at = timezone.now() + timedelta(hours=1)

        self.host_client = self.client_class()
        host_session_key = self._ensure_session_key(self.host_client)
        self.host_player = Player.objects.create(
            room=self.room,
            session_key=host_session_key,
            display_name="Host Alex",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            current_score=42,
            session_expires_at=session_expires_at,
        )

        self.member_client = self.client_class()
        member_session_key = self._ensure_session_key(self.member_client)
        self.member_player = Player.objects.create(
            room=self.room,
            session_key=member_session_key,
            display_name="Jamie",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            current_score=7,
            session_expires_at=session_expires_at,
        )

        self.room.host = self.host_player
        self.room.save(update_fields=("host",))

    def test_host_can_start_game_and_create_first_active_round(self):
        response = self.host_client.post(self.url)

        self.assertEqual(response.status_code, 201)
        data = response.json()

        self.room.refresh_from_db()
        game = Game.objects.get(id=data["game_id"])
        first_round = Round.objects.get(id=data["round_id"])

        self.assertEqual(self.room.status, Room.Status.IN_PROGRESS)
        self.assertEqual(game.room_id, self.room.id)
        self.assertEqual(first_round.game_id, game.id)
        self.assertEqual(first_round.sequence_number, 1)
        self.assertIsNone(first_round.status)
        self.assertIsNone(first_round.ended_at)
        self.assertIn(first_round.drawer_participant_id, {self.host_player.id, self.member_player.id})
        self.assertIn(
            first_round.selected_game_word.text,
            list(game.snapshot_words.values_list("text", flat=True)),
        )
        self.assertEqual(
            GameWord.objects.filter(game=game).count(),
            3,
        )
        self.assertEqual(data["room_status"], Room.Status.IN_PROGRESS)
        self.assertEqual(
            data["room"],
            {
                "join_code": self.room.join_code,
                "status": Room.Status.IN_PROGRESS,
            },
        )
        self.assertEqual(
            data["game"],
            {
                "id": game.id,
                "status": game.status,
                "word_count": 3,
            },
        )
        self.assertEqual(
            data["first_round"],
            {
                "id": first_round.id,
                "sequence_number": 1,
                "status": None,
                "drawer_participant_id": first_round.drawer_participant_id,
                "drawer_nickname": first_round.drawer_nickname,
                "selected_game_word_id": first_round.selected_game_word_id,
            },
        )

        self.host_player.refresh_from_db()
        self.member_player.refresh_from_db()
        self.assertEqual(self.host_player.current_score, 0)
        self.assertEqual(self.member_player.current_score, 0)

    def test_start_game_ignores_client_attempt_to_choose_first_drawer_or_word(self):
        spectator = Player.objects.create(
            room=self.room,
            session_key="spectator-session",
            display_name="Spectator",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.SPECTATING,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )
        out_of_snapshot_word = Word.objects.create(text="dragonfruit")

        response = self.host_client.post(
            self.url,
            data=json.dumps(
                {
                    "drawer_participant_id": spectator.id,
                    "selected_game_word_id": out_of_snapshot_word.id,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        round_id = response.json()["round_id"]
        first_round = Round.objects.select_related("selected_game_word").get(id=round_id)

        self.assertNotEqual(first_round.drawer_participant_id, spectator.id)
        self.assertNotEqual(first_round.selected_game_word_id, out_of_snapshot_word.id)

    def test_start_game_returns_clear_error_when_room_word_setup_is_missing(self):
        empty_pack = WordPack.objects.create(name="Empty Pack")
        self.room.word_pack = empty_pack
        self.room.save(update_fields=("word_pack",))

        response = self.host_client.post(self.url)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"],
            "The room's selected word list has no words.",
        )
        self.assertEqual(Game.objects.filter(room=self.room).count(), 0)

    def test_non_host_participant_cannot_start_game(self):
        response = self.member_client.post(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Only the room host can start a game.")
        self.assertEqual(Game.objects.filter(room=self.room).count(), 0)

    def test_non_participant_cannot_start_game(self):
        outsider_client = self.client_class()
        self._ensure_session_key(outsider_client)

        response = outsider_client.post(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"],
            "This guest session is not a participant in this room.",
        )
        self.assertEqual(Game.objects.filter(room=self.room).count(), 0)

    def test_start_game_requires_post(self):
        response = self.host_client.get(self.url)

        self.assertEqual(response.status_code, 405)
        self.assertEqual(Game.objects.filter(room=self.room).count(), 0)

    def test_start_game_returns_conflict_when_room_is_not_in_lobby(self):
        self.room.status = Room.Status.IN_PROGRESS
        self.room.save(update_fields=("status",))

        response = self.host_client.post(self.url)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(Game.objects.filter(room=self.room).count(), 0)

    def test_start_game_returns_conflict_when_fewer_than_two_eligible_participants(self):
        self.member_player.participation_status = Player.ParticipationStatus.SPECTATING
        self.member_player.save(update_fields=("participation_status", "updated_at"))

        response = self.host_client.post(self.url)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(Game.objects.filter(room=self.room).count(), 0)


class RoomsAdminRegistrationTests(SimpleTestCase):
    def test_room_and_player_models_are_registered_in_admin(self):
        self.assertIsInstance(admin.site._registry.get(Room), RoomAdmin)
        self.assertIsInstance(admin.site._registry.get(Player), PlayerAdmin)

    def test_room_admin_configuration_matches_expected_setup(self):
        room_admin = admin.site._registry[Room]

        self.assertEqual(
            room_admin.list_display,
            (
                "id",
                "join_code",
                "name",
                "visibility",
                "status",
                "max_players",
                "host",
                "empty_since",
                "created_at",
                "updated_at",
            ),
        )
        self.assertEqual(
            room_admin.list_filter,
            ("visibility", "status", "created_at", "updated_at"),
        )
        self.assertEqual(room_admin.search_fields, ("join_code", "name", "host__display_name"))
        self.assertEqual(room_admin.raw_id_fields, ("host",))
        self.assertEqual(room_admin.readonly_fields, ("created_at", "updated_at"))
        self.assertEqual(room_admin.list_select_related, ("host",))

    def test_player_admin_configuration_matches_expected_setup(self):
        player_admin = admin.site._registry[Player]

        self.assertEqual(
            player_admin.list_display,
            (
                "id",
                "display_name",
                "room",
                "connection_status",
                "participation_status",
                "current_score",
                "session_expires_at",
                "last_seen_at",
                "created_at",
                "updated_at",
            ),
        )
        self.assertEqual(
            player_admin.list_filter,
            ("connection_status", "participation_status", "created_at"),
        )
        self.assertEqual(
            player_admin.search_fields,
            ("display_name", "session_key", "room__join_code", "room__name"),
        )
        self.assertEqual(player_admin.raw_id_fields, ("room",))
        self.assertEqual(player_admin.readonly_fields, ("created_at", "updated_at"))
        self.assertEqual(player_admin.list_select_related, ("room",))
