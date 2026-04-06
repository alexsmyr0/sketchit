import json
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase

from rooms.models import Player, Room


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
        response_data = response.json()

        self.assertEqual(room.name, "Friday Sketches")
        self.assertEqual(room.visibility, Room.Visibility.PRIVATE)
        self.assertEqual(room.status, Room.Status.LOBBY)
        self.assertEqual(len(room.join_code), 8)
        self.assertEqual(room.host_id, player.id)

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
