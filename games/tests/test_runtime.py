"""
Unit tests for games.runtime

These tests exercise the runtime sync logic in-process using fakeredis so no
real Redis server is required.  Database access goes through Django's normal
TestCase transaction rollback, so each test starts with a clean slate.
"""

from datetime import timedelta

import fakeredis
from django.test import TestCase, override_settings
from django.utils import timezone

from games import redis as game_redis
from games import runtime as game_runtime
from rooms.models import Room


def _make_fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


def _make_room(status: str = Room.Status.LOBBY) -> Room:
    from words.models import Word, WordPack, WordPackEntry

    wp = WordPack.objects.create(name="Test Pack")
    word = Word.objects.create(text="apple")
    WordPackEntry.objects.create(word_pack=wp, word=word)

    return Room.objects.create(
        name="Test Room",
        join_code="TESTRM01",
        status=status,
        word_pack=wp,
    )


def _seed_intermission_turn_state(client: fakeredis.FakeRedis, join_code: str) -> None:
    """Pre-populate Redis with a plausible intermission turn_state."""
    deadline = (timezone.now() + timedelta(seconds=10)).isoformat()
    game_redis.set_turn_state(
        client,
        join_code,
        {
            "phase": "intermission",
            "status": "intermission",
            "game_id": "1",
            "round_id": "42",
            "drawer_participant_id": "7",
            "completed_round_sequence": "1",
            "ended_at": timezone.now().isoformat(),
            "deadline_at": deadline,
            "eligible_guesser_ids": "[8]",
            "correct_guesser_ids": "[8]",
            "round_timer_sequence": "5",
            "intermission_timer_sequence": "0",
            "leaderboard_timer_sequence": "0",
            "drawer_disconnect_deadline_at": "",
            "last_timer_server_timestamp": timezone.now().isoformat(),
        },
    )


@override_settings(SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True)
class GetSyncEventsStaleRedisTests(TestCase):
    """Validate that get_sync_events_for_player treats MySQL as the tiebreaker."""

    def setUp(self):
        game_runtime.reset_runtime_state_for_tests()
        self.fake_redis = _make_fake_redis()
        game_runtime._redis_client = self.fake_redis

    def tearDown(self):
        game_runtime.reset_runtime_state_for_tests()

    # ------------------------------------------------------------------
    # Core regression test for the stuck-intermission-overlay bug
    # ------------------------------------------------------------------

    def test_stale_intermission_redis_with_lobby_room_returns_no_events_and_clears_redis(self):
        """Redis says intermission; MySQL says lobby → sync returns [] and wipes Redis."""
        room = _make_room(status=Room.Status.LOBBY)
        _seed_intermission_turn_state(self.fake_redis, room.join_code)

        # Redis key should exist before the call
        turn_state_before = game_redis.get_turn_state(self.fake_redis, room.join_code)
        self.assertNotEqual(turn_state_before, {}, "pre-condition: Redis key must exist")

        result = game_runtime.get_sync_events_for_player(room.join_code, player_id=7)

        # The function must return no events
        self.assertEqual(result, [], "Expected no sync events for a lobby-status room with stale Redis state")

        # The Redis key must have been cleaned up
        turn_state_after = game_redis.get_turn_state(self.fake_redis, room.join_code)
        self.assertEqual(turn_state_after, {}, "Expected Redis turn_state to be cleared after validation")

    def test_stale_intermission_redis_room_not_found_returns_no_events_and_clears_redis(self):
        """Redis has turn_state for a join_code with no matching Room → [] and clears Redis."""
        phantom_join_code = "PHANTOM1"
        _seed_intermission_turn_state(self.fake_redis, phantom_join_code)

        result = game_runtime.get_sync_events_for_player(phantom_join_code, player_id=7)

        self.assertEqual(result, [])
        turn_state_after = game_redis.get_turn_state(self.fake_redis, phantom_join_code)
        self.assertEqual(turn_state_after, {})

    def test_empty_redis_returns_no_events(self):
        """No Redis state at all → [] regardless of room status."""
        room = _make_room(status=Room.Status.IN_PROGRESS)

        result = game_runtime.get_sync_events_for_player(room.join_code, player_id=7)

        self.assertEqual(result, [])

    def test_in_progress_room_with_valid_intermission_state_passes_through(self):
        """Redis says intermission AND MySQL says IN_PROGRESS → events are returned (no regression)."""
        room = _make_room(status=Room.Status.IN_PROGRESS)
        _seed_intermission_turn_state(self.fake_redis, room.join_code)

        result = game_runtime.get_sync_events_for_player(room.join_code, player_id=7)

        event_types = [e["type"] for e in result]
        self.assertIn("round.state", event_types, "round.state must be returned for a genuine IN_PROGRESS intermission")
        self.assertIn("round.intermission_timer", event_types)

        # Redis must NOT have been cleared
        turn_state_after = game_redis.get_turn_state(self.fake_redis, room.join_code)
        self.assertNotEqual(turn_state_after, {}, "Redis must be preserved for legitimate IN_PROGRESS intermission")
