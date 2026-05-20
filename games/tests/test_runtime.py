"""
Unit tests for games.runtime

These tests exercise the runtime sync logic in-process using fakeredis so no
real Redis server is required.  Database access goes through Django's normal
TestCase transaction rollback, so each test starts with a clean slate.
"""

import json
from datetime import timedelta
from unittest.mock import patch

import fakeredis
from django.test import TestCase, override_settings
from django.utils import timezone

from games import redis as game_redis
from games import runtime as game_runtime
from games.models import Game, GameStatus, GameWord, Round
from rooms.models import Player, Room, RoomGameMode


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


@override_settings(
    SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True,
    SKETCHIT_ROUND_DURATION_SECONDS=90,
)
class StartRoundRuntimeTurnStateTests(TestCase):
    def setUp(self):
        game_runtime.reset_runtime_state_for_tests()
        self.fake_redis = _make_fake_redis()
        game_runtime._redis_client = self.fake_redis

    def tearDown(self):
        game_runtime.reset_runtime_state_for_tests()

    def test_start_round_runtime_writes_duo_eligible_guesser_ids_excluding_both_drawers(self):
        room = _make_room(status=Room.Status.IN_PROGRESS)
        session_expires_at = timezone.now() + timedelta(hours=1)

        drawer = Player.objects.create(
            room=room,
            session_key="drawer-session",
            display_name="Drawer",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=session_expires_at,
        )
        second_drawer = Player.objects.create(
            room=room,
            session_key="second-drawer-session",
            display_name="Second Drawer",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=session_expires_at,
        )
        first_guesser = Player.objects.create(
            room=room,
            session_key="first-guesser-session",
            display_name="First Guesser",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=session_expires_at,
        )
        second_guesser = Player.objects.create(
            room=room,
            session_key="second-guesser-session",
            display_name="Second Guesser",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=session_expires_at,
        )

        game = Game.objects.create(
            room=room,
            status=GameStatus.IN_PROGRESS,
            game_mode=RoomGameMode.DUO,
        )
        game_word = GameWord.objects.create(game=game, text="apple")
        round_started_at = timezone.now()
        round = Round.objects.create(
            game=game,
            drawer_participant=drawer,
            drawer_nickname=drawer.display_name,
            second_drawer_participant=second_drawer,
            second_drawer_nickname=second_drawer.display_name,
            selected_game_word=game_word,
            sequence_number=1,
            started_at=round_started_at,
        )
        late_joiner = Player.objects.create(
            room=room,
            session_key="late-joiner-session",
            display_name="Late Joiner",
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=session_expires_at,
        )

        with patch("games.runtime._start_round_timer"), patch(
            "games.runtime.broadcast_room_event"
        ), patch("games.runtime.broadcast_player_event"):
            game_runtime.start_round_runtime(round.id)

        turn_state = game_redis.get_turn_state(self.fake_redis, room.join_code)
        self.assertEqual(turn_state.get("drawer_participant_id"), str(drawer.id))
        self.assertEqual(
            turn_state.get("second_drawer_participant_id"),
            str(second_drawer.id),
        )
        self.assertEqual(
            json.loads(turn_state.get("eligible_guesser_ids", "[]")),
            sorted([first_guesser.id, second_guesser.id]),
        )
        self.assertNotIn(drawer.id, json.loads(turn_state["eligible_guesser_ids"]))
        self.assertNotIn(second_drawer.id, json.loads(turn_state["eligible_guesser_ids"]))
        self.assertNotIn(late_joiner.id, json.loads(turn_state["eligible_guesser_ids"]))
