"""Tests for room-domain participant lifecycle services."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import fakeredis
from django.test import TestCase
from django.utils import timezone

from games import redis as game_redis
from rooms import redis as room_redis
from rooms.models import Player, Room
from rooms.services import (
    connect_participant,
    delete_room_if_empty_grace_expired,
    disconnect_participant,
    enter_empty_room_grace,
    get_empty_room_cleanup_deadline,
    leave_participant,
    restore_room_from_empty_grace,
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

    def test_connect_and_disconnect_use_canonical_room_join_code_for_presence(self):
        lowercase_join_code = self.room.join_code.lower()

        connect_participant(
            redis_client=self.redis_client,
            player_id=self.player.id,
            join_code=lowercase_join_code,
            session_key=self.player.session_key,
            connection_id="chan-1",
        )

        self.assertTrue(
            room_redis.is_present(
                self.redis_client,
                self.room.join_code,
                self.player.session_key,
            )
        )
        self.assertFalse(
            room_redis.is_present(
                self.redis_client,
                lowercase_join_code,
                self.player.session_key,
            )
        )

        disconnect_participant(
            redis_client=self.redis_client,
            player_id=self.player.id,
            join_code=lowercase_join_code,
            session_key=self.player.session_key,
            connection_id="chan-1",
        )

        self.assertFalse(
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
        self.assertEqual(self.room.status, Room.Status.EMPTY_GRACE)
        self.assertIsNotNone(self.room.empty_since)
        self.assertIsNotNone(
            game_redis.get_deadline(
                self.redis_client,
                self.room.join_code,
                "cleanup",
            )
        )

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
        self.assertEqual(self.room.status, Room.Status.LOBBY)
        self.assertIsNone(self.room.empty_since)
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
        self.assertEqual(self.room.status, Room.Status.LOBBY)
        self.assertIsNone(self.room.empty_since)
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

    def test_connect_participant_rejects_mismatched_join_code(self):
        with self.assertRaisesMessage(
            ValueError,
            "Participant does not belong to the given room join code.",
        ):
            connect_participant(
                redis_client=self.redis_client,
                player_id=self.player.id,
                join_code="WRONG123",
                session_key=self.player.session_key,
                connection_id="chan-1",
            )


class EmptyRoomGraceServiceTests(TestCase):
    """Service-level coverage for empty-room grace entry, restore, and expiry."""

    def setUp(self):
        self.redis_client = fakeredis.FakeRedis()
        self.room = Room.objects.create(
            name="Grace Room",
            join_code="GRACE123",
            visibility=Room.Visibility.PRIVATE,
        )

    def test_enter_empty_room_grace_sets_status_timestamp_and_cleanup_deadline(self):
        entered_at = timezone.now()

        enter_empty_room_grace(
            redis_client=self.redis_client,
            room_id=self.room.id,
            now=entered_at,
        )

        self.room.refresh_from_db()

        self.assertEqual(self.room.status, Room.Status.EMPTY_GRACE)
        self.assertEqual(self.room.empty_since, entered_at)
        self.assertEqual(
            game_redis.get_deadline(
                self.redis_client,
                self.room.join_code,
                "cleanup",
            ),
            get_empty_room_cleanup_deadline(empty_since=entered_at).isoformat(),
        )

    def test_enter_empty_room_grace_preserves_existing_timestamp(self):
        original_empty_since = timezone.now() - timedelta(minutes=3)
        self.room.status = Room.Status.EMPTY_GRACE
        self.room.empty_since = original_empty_since
        self.room.save(update_fields=["status", "empty_since", "updated_at"])

        enter_empty_room_grace(
            redis_client=self.redis_client,
            room_id=self.room.id,
            now=timezone.now(),
        )

        self.room.refresh_from_db()

        self.assertEqual(self.room.empty_since, original_empty_since)
        self.assertEqual(
            game_redis.get_deadline(
                self.redis_client,
                self.room.join_code,
                "cleanup",
            ),
            get_empty_room_cleanup_deadline(
                empty_since=original_empty_since,
            ).isoformat(),
        )

    def test_enter_empty_room_grace_rejects_non_empty_room(self):
        Player.objects.create(
            room=self.room,
            session_key="session-123",
            display_name="Alex",
            connection_status=Player.ConnectionStatus.DISCONNECTED,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )

        with self.assertRaisesMessage(
            ValueError,
            "Cannot move a non-empty room into empty grace.",
        ):
            enter_empty_room_grace(
                redis_client=self.redis_client,
                room_id=self.room.id,
            )

    def test_restore_room_from_empty_grace_returns_room_to_lobby(self):
        enter_empty_room_grace(
            redis_client=self.redis_client,
            room_id=self.room.id,
            now=timezone.now(),
        )

        restore_room_from_empty_grace(
            redis_client=self.redis_client,
            room_id=self.room.id,
        )

        self.room.refresh_from_db()

        self.assertEqual(self.room.status, Room.Status.LOBBY)
        self.assertIsNone(self.room.empty_since)
        self.assertIsNone(
            game_redis.get_deadline(
                self.redis_client,
                self.room.join_code,
                "cleanup",
            )
        )

    def test_restore_room_from_empty_grace_rejects_non_empty_grace_room(self):
        with self.assertRaisesMessage(
            ValueError,
            "Only rooms in empty grace can be restored.",
        ):
            restore_room_from_empty_grace(
                redis_client=self.redis_client,
                room_id=self.room.id,
            )

    def test_delete_room_if_empty_grace_expired_keeps_room_before_deadline(self):
        entered_at = timezone.now()
        enter_empty_room_grace(
            redis_client=self.redis_client,
            room_id=self.room.id,
            now=entered_at,
        )

        deleted = delete_room_if_empty_grace_expired(
            redis_client=self.redis_client,
            room_id=self.room.id,
            now=entered_at + timedelta(minutes=9, seconds=59),
        )

        self.assertFalse(deleted)
        self.assertTrue(Room.objects.filter(pk=self.room.id).exists())

    def test_delete_room_if_empty_grace_expired_deletes_room_after_deadline(self):
        entered_at = timezone.now()
        enter_empty_room_grace(
            redis_client=self.redis_client,
            room_id=self.room.id,
            now=entered_at,
        )

        deleted = delete_room_if_empty_grace_expired(
            redis_client=self.redis_client,
            room_id=self.room.id,
            now=entered_at + timedelta(minutes=10),
        )

        self.assertTrue(deleted)
        self.assertFalse(Room.objects.filter(pk=self.room.id).exists())
        self.assertIsNone(
            game_redis.get_deadline(
                self.redis_client,
                "GRACE123",
                "cleanup",
            )
        )
