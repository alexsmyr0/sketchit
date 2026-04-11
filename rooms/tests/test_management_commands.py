"""Tests for room-related management commands."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import fakeredis
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from games import redis as game_redis
from rooms.models import Room
from rooms.services import get_empty_room_cleanup_deadline


class CleanupEmptyRoomsCommandTests(TestCase):
    """Verify the empty-room cleanup management command delegates correctly."""

    @patch("rooms.management.commands.cleanup_empty_rooms._get_redis_client")
    def test_cleanup_empty_rooms_command_deletes_expired_rooms_and_reports_count(
        self,
        get_redis_client,
    ):
        fake_redis = fakeredis.FakeRedis()
        get_redis_client.return_value = fake_redis
        now = timezone.now()
        expired_room = Room.objects.create(
            name="Expired Room",
            join_code="CMDROOM1",
            visibility=Room.Visibility.PRIVATE,
            status=Room.Status.EMPTY_GRACE,
            empty_since=now - timedelta(minutes=11),
        )
        fresh_room = Room.objects.create(
            name="Fresh Room",
            join_code="CMDROOM2",
            visibility=Room.Visibility.PRIVATE,
            status=Room.Status.EMPTY_GRACE,
            empty_since=now - timedelta(minutes=3),
        )
        game_redis.set_deadline(
            fake_redis,
            expired_room.join_code,
            "cleanup",
            get_empty_room_cleanup_deadline(
                empty_since=expired_room.empty_since,
            ).isoformat(),
        )
        game_redis.set_deadline(
            fake_redis,
            fresh_room.join_code,
            "cleanup",
            get_empty_room_cleanup_deadline(
                empty_since=fresh_room.empty_since,
            ).isoformat(),
        )

        output = StringIO()
        with patch("rooms.services.timezone.now", return_value=now):
            call_command("cleanup_empty_rooms", stdout=output)

        self.assertIn("Deleted 1 expired empty room(s).", output.getvalue())
        self.assertFalse(Room.objects.filter(pk=expired_room.id).exists())
        self.assertTrue(Room.objects.filter(pk=fresh_room.id).exists())
        self.assertIsNone(
            game_redis.get_deadline(
                fake_redis,
                expired_room.join_code,
                "cleanup",
            )
        )
