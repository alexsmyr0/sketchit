"""Tests for room-domain participant lifecycle services."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import fakeredis
from django.test import TestCase
from django.utils import timezone

from rooms import redis as room_redis
from rooms.models import Player, Room
from rooms.services import (
    connect_participant,
    disconnect_participant,
    leave_participant,
)


class ParticipantLifecycleServiceTests(TestCase):
    """Service-level coverage for participant connection lifecycle rules."""

    def setUp(self):
        self.redis_client = fakeredis.FakeRedis()
        self.room = Room.objects.create(
            name="Lifecycle Room",
            join_code="LIFE1234",
            visibility=Room.Visibility.PRIVATE,
        )
        self.player = Player.objects.create(
            room=self.room,
            session_key="session-123",
            display_name="Alex",
            connection_status=Player.ConnectionStatus.DISCONNECTED,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )
        self.room.host = self.player
        self.room.save(update_fields=["host"])

    def test_connect_participant_marks_player_connected_and_tracks_presence(self):
        connect_participant(
            redis_client=self.redis_client,
            player_id=self.player.id,
            join_code=self.room.join_code,
            session_key=self.player.session_key,
            connection_id="chan-1",
        )

        self.player.refresh_from_db()

        self.assertEqual(
            self.player.connection_status,
            Player.ConnectionStatus.CONNECTED,
        )
        self.assertIsNotNone(self.player.last_seen_at)
        self.assertTrue(
            room_redis.is_present(
                self.redis_client,
                self.room.join_code,
                self.player.session_key,
            )
        )

    def test_disconnect_participant_marks_player_disconnected_after_last_socket(self):
        connect_participant(
            redis_client=self.redis_client,
            player_id=self.player.id,
            join_code=self.room.join_code,
            session_key=self.player.session_key,
            connection_id="chan-1",
        )

        disconnect_participant(
            redis_client=self.redis_client,
            player_id=self.player.id,
            join_code=self.room.join_code,
            session_key=self.player.session_key,
            connection_id="chan-1",
        )

        self.player.refresh_from_db()

        self.assertEqual(
            self.player.connection_status,
            Player.ConnectionStatus.DISCONNECTED,
        )
        self.assertFalse(
            room_redis.is_present(
                self.redis_client,
                self.room.join_code,
                self.player.session_key,
            )
        )

    def test_disconnect_participant_keeps_player_connected_when_another_socket_remains(self):
        connect_participant(
            redis_client=self.redis_client,
            player_id=self.player.id,
            join_code=self.room.join_code,
            session_key=self.player.session_key,
            connection_id="chan-1",
        )
        connect_participant(
            redis_client=self.redis_client,
            player_id=self.player.id,
            join_code=self.room.join_code,
            session_key=self.player.session_key,
            connection_id="chan-2",
        )

        disconnect_participant(
            redis_client=self.redis_client,
            player_id=self.player.id,
            join_code=self.room.join_code,
            session_key=self.player.session_key,
            connection_id="chan-1",
        )

        self.player.refresh_from_db()

        self.assertEqual(
            self.player.connection_status,
            Player.ConnectionStatus.CONNECTED,
        )
        self.assertTrue(
            room_redis.is_present(
                self.redis_client,
                self.room.join_code,
                self.player.session_key,
            )
        )

    def test_leave_participant_deletes_membership_and_clears_room_presence(self):
        connect_participant(
            redis_client=self.redis_client,
            player_id=self.player.id,
            join_code=self.room.join_code,
            session_key=self.player.session_key,
            connection_id="chan-1",
        )
        connect_participant(
            redis_client=self.redis_client,
            player_id=self.player.id,
            join_code=self.room.join_code,
            session_key=self.player.session_key,
            connection_id="chan-2",
        )

        leave_participant(
            redis_client=self.redis_client,
            player_id=self.player.id,
        )

        self.assertFalse(Player.objects.filter(pk=self.player.id).exists())
        self.assertFalse(
            room_redis.is_present(
                self.redis_client,
                self.room.join_code,
                self.player.session_key,
            )
        )
        self.assertEqual(
            self.redis_client.exists(
                room_redis._presence_connections_key(
                    self.room.join_code,
                    self.player.session_key,
                )
            ),
            0,
        )
        self.room.refresh_from_db()
        self.assertIsNone(self.room.host)

    @patch("rooms.services.random.choice")
    def test_leave_participant_reassigns_host_when_current_host_leaves(
        self,
        random_choice,
    ):
        second_player = Player.objects.create(
            room=self.room,
            session_key="session-456",
            display_name="Jamie",
            connection_status=Player.ConnectionStatus.DISCONNECTED,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )
        third_player = Player.objects.create(
            room=self.room,
            session_key="session-789",
            display_name="Morgan",
            connection_status=Player.ConnectionStatus.DISCONNECTED,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )
        random_choice.return_value = third_player

        leave_participant(
            redis_client=self.redis_client,
            player_id=self.player.id,
        )

        self.room.refresh_from_db()

        self.assertEqual(self.room.host_id, third_player.id)
        self.assertFalse(Player.objects.filter(pk=self.player.id).exists())
        random_choice.assert_called_once()

    def test_leave_participant_keeps_existing_host_when_non_host_leaves(self):
        non_host_player = Player.objects.create(
            room=self.room,
            session_key="session-456",
            display_name="Jamie",
            connection_status=Player.ConnectionStatus.DISCONNECTED,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )

        leave_participant(
            redis_client=self.redis_client,
            player_id=non_host_player.id,
        )

        self.room.refresh_from_db()

        self.assertEqual(self.room.host_id, self.player.id)
        self.assertFalse(Player.objects.filter(pk=non_host_player.id).exists())

    def test_connect_participant_rejects_mismatched_session_key(self):
        with self.assertRaisesMessage(
            ValueError,
            "Participant does not belong to the given session key.",
        ):
            connect_participant(
                redis_client=self.redis_client,
                player_id=self.player.id,
                join_code=self.room.join_code,
                session_key="wrong-session",
                connection_id="chan-1",
            )
