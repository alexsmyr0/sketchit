import json
from unittest.mock import patch
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from rooms.models import Player, Room


class RoomLobbyUITests(TestCase):
    def _ensure_session_key(self, client):
        session = client.session
        session.save()
        return session.session_key

    def setUp(self):
        self.room = Room.objects.create(
            name="Test Room",
            join_code="LOBBY123",
            visibility=Room.Visibility.PUBLIC,
            status=Room.Status.LOBBY
        )
        
        # Setup Host
        self.host_client = self.client_class()
        host_session_key = self._ensure_session_key(self.host_client)
        self.host = Player.objects.create(
            room=self.room,
            display_name="HostUser",
            session_key=host_session_key,
            connection_status=Player.ConnectionStatus.CONNECTED,
            session_expires_at=timezone.now() + timedelta(days=1)
        )
        self.room.host = self.host
        self.room.save()

        # Setup Guest
        self.guest_client = self.client_class()
        guest_session_key = self._ensure_session_key(self.guest_client)
        self.guest = Player.objects.create(
            room=self.room,
            display_name="GuestUser",
            session_key=guest_session_key,
            connection_status=Player.ConnectionStatus.CONNECTED,
            session_expires_at=timezone.now() + timedelta(days=1)
        )

    def test_lobby_template_renders_host_controls_for_host(self):
        response = self.host_client.get(reverse("room-lobby-state", args=[self.room.join_code]), HTTP_ACCEPT="text/html")
        content = response.content.decode()
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="host-controls"')
        self.assertContains(response, 'id="settings-form"')
        self.assertContains(response, 'id="start-game-button"')
        self.assertContains(response, 'id="host-controls-note"')
        self.assertContains(response, 'id="guest-view"')
        self.assertIn('id="guest-view" class="guest-view" hidden', content)
        self.assertNotIn('id="host-controls" class="host-controls" hidden', content)
        
        # Verify join URL
        # Note: test server host might varies, checking for relative path link
        expected_join_path = reverse('join-room', args=[self.room.join_code])
        self.assertContains(response, expected_join_path)

    def test_lobby_template_keeps_host_controls_hidden_for_guest(self):
        response = self.guest_client.get(reverse("room-lobby-state", args=[self.room.join_code]), HTTP_ACCEPT="text/html")
        content = response.content.decode()
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="host-controls"')
        self.assertContains(response, 'id="guest-view"')
        self.assertIn('id="host-controls" class="host-controls" hidden', content)
        self.assertNotIn('id="guest-view" class="guest-view" hidden', content)
        self.assertContains(response, "Waiting for the host to start the game...")

    def test_lobby_participant_list_labels(self):
        response = self.host_client.get(reverse("room-lobby-state", args=[self.room.join_code]), HTTP_ACCEPT="text/html")
        
        # Host should see crown by their name and "(You)" label
        self.assertContains(response, "👑")
        self.assertContains(response, "(You)")
        
        # Verify guest listing in the same response
        self.assertContains(response, "GuestUser")

    def test_room_page_template_renders_gameplay_shell_contract(self):
        response = self.host_client.get(
            reverse("room-lobby-state", args=[self.room.join_code]),
            HTTP_ACCEPT="text/html",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="game-view"')
        self.assertContains(response, 'id="guess-input"')
        self.assertContains(response, 'id="timer-bar"')
        self.assertContains(response, 'id="intermission-overlay"')
        self.assertContains(response, 'id="intermission-results"')
        self.assertContains(response, 'id="intermission-return-button"')

    def test_lobby_template_disables_start_until_two_eligible_players_exist(self):
        self.guest.connection_status = Player.ConnectionStatus.DISCONNECTED
        self.guest.participation_status = Player.ParticipationStatus.SPECTATING
        self.guest.save(update_fields=["connection_status", "participation_status", "updated_at"])

        response = self.host_client.get(
            reverse("room-lobby-state", args=[self.room.join_code]),
            HTTP_ACCEPT="text/html",
        )
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="start-game-button" type="button" class="action-button primary" disabled', content)
        self.assertContains(response, "Need at least 2 eligible players to start.")

    @patch("rooms.views.schedule_room_state_broadcast_after_commit")
    def test_update_settings_triggers_broadcast(self, mock_broadcast):
        url = reverse("update-lobby-settings", args=[self.room.join_code])
        payload = {
            "name": "Updated Room Name",
            "visibility": "private"
        }
        
        response = self.host_client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN="dummy_token"
        )
        
        self.assertEqual(response.status_code, 200)
        self.room.refresh_from_db()
        self.assertEqual(self.room.name, "Updated Room Name")
        self.assertEqual(self.room.visibility, Room.Visibility.PRIVATE)
        
        # Verify broadcast was scheduled
        mock_broadcast.assert_called_once_with(
            join_code=self.room.join_code,
            room_id=self.room.id
        )

    @patch("rooms.views.schedule_room_state_broadcast_after_commit")
    def test_start_game_triggers_broadcast(self, mock_broadcast):
        url = reverse("start-game", args=[self.room.join_code])
        
        # Mock start_game_for_room to return a structure the view can handle
        mock_started_game = patch("rooms.views.start_game_for_room").start()
        mock_started_game.return_value.game.id = 1
        mock_started_game.return_value.game.status = "in_progress"
        mock_started_game.return_value.game.snapshot_words.count.return_value = 5
        mock_started_game.return_value.first_round.id = 10
        mock_started_game.return_value.first_round.sequence_number = 1
        mock_started_game.return_value.first_round.status = "active"
        mock_started_game.return_value.first_round.drawer_participant_id = self.host.id
        mock_started_game.return_value.first_round.drawer_nickname = self.host.display_name
        mock_started_game.return_value.first_round.selected_game_word_id = 100

        try:
            response = self.host_client.post(url)
            self.assertEqual(response.status_code, 201) # View returns 201 on success
            mock_started_game.assert_called_once()
        finally:
            patch("rooms.views.start_game_for_room").stop()
        
        # Verify broadcast was scheduled by the view
        mock_broadcast.assert_called_once_with(
            join_code=self.room.join_code,
            room_id=self.room.id
        )
