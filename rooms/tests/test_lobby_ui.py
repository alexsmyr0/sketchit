import json
from unittest.mock import patch
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from rooms.models import Player, Room
from words.models import Word, WordPack, WordPackEntry


class _FakeStartedGame:
    """Small test double matching the fields the start-game view reads."""

    def __init__(self, *, game, first_round):
        self.game = game
        self.first_round = first_round


class RoomLobbyUITests(TestCase):
    def _ensure_session_key(self, client):
        """Create a real session key so the view can identify the test client."""
        session = client.session
        session.save()
        return session.session_key

    def setUp(self):
        # Room.word_pack is required in the real MySQL-backed schema. Creating
        # the pack explicitly keeps these template tests independent from any
        # seed data that may or may not exist in a reused test database.
        self.word_pack = WordPack.objects.create(name="Lobby Test Pack")
        self.word = Word.objects.create(text="rocket")
        WordPackEntry.objects.create(word_pack=self.word_pack, word=self.word)

        self.room = Room.objects.create(
            name="Test Room",
            join_code="LOBBY123",
            visibility=Room.Visibility.PUBLIC,
            status=Room.Status.LOBBY,
            word_pack=self.word_pack,
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

        # The view only needs a narrow StartedGame-shaped object here; using a
        # context-managed patch prevents this test double from leaking into
        # later view tests in the full MySQL suite.
        fake_game = type("FakeGame", (), {})()
        fake_game.id = 1
        fake_game.status = "in_progress"
        fake_game.snapshot_words = type(
            "FakeSnapshotWords",
            (),
            {"count": lambda self: 5},
        )()
        fake_round = type("FakeRound", (), {})()
        fake_round.id = 10
        fake_round.sequence_number = 1
        fake_round.status = "active"
        fake_round.drawer_participant_id = self.host.id
        fake_round.drawer_nickname = self.host.display_name
        fake_round.selected_game_word_id = 100
        fake_started_game = _FakeStartedGame(game=fake_game, first_round=fake_round)

        with patch("rooms.views.start_game_for_room", return_value=fake_started_game) as mock_started_game:
            response = self.host_client.post(url)
            self.assertEqual(response.status_code, 201) # View returns 201 on success
            mock_started_game.assert_called_once()

        # Verify broadcast was scheduled by the view
        mock_broadcast.assert_called_once_with(
            join_code=self.room.join_code,
            room_id=self.room.id
        )
