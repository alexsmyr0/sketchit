"""
Unit tests for games.runtime

These tests exercise the runtime sync logic in-process using fakeredis so no
real Redis server is required.  Database access goes through Django's normal
TestCase transaction rollback, so each test starts with a clean slate.
"""

import json
import threading
from datetime import timedelta
from unittest.mock import patch

import fakeredis
from django.test import TestCase, override_settings
from django.utils import timezone

from games import redis as game_redis
from games import runtime as game_runtime
from games import services as game_services
from games.models import Game, GameStatus, GameWord, Round, RoundStatus
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


@override_settings(
    SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True,
    SKETCHIT_ROUND_DURATION_SECONDS=90,
    SKETCHIT_DRAWER_DISCONNECT_GRACE_SECONDS=15,
)
class DuoDrawerDisconnectRuntimeTests(TestCase):
    def setUp(self):
        game_runtime.reset_runtime_state_for_tests()
        self.fake_redis = _make_fake_redis()
        game_runtime._redis_client = self.fake_redis
        self._original_services_redis_client = game_services._redis_client
        game_services._redis_client = self.fake_redis

        self.room = _make_room(status=Room.Status.IN_PROGRESS)
        self.room.game_mode = RoomGameMode.DUO
        self.room.save(update_fields=("game_mode", "updated_at"))
        session_expires_at = timezone.now() + timedelta(hours=1)
        self.primary_drawer = self._create_player("primary-drawer", "Primary")
        self.second_drawer = self._create_player("second-drawer", "Second")
        self.guesser = self._create_player("guesser", "Guesser")
        self.other_guesser = self._create_player("other-guesser", "Other Guesser")
        for player in (
            self.primary_drawer,
            self.second_drawer,
            self.guesser,
            self.other_guesser,
        ):
            player.session_expires_at = session_expires_at
            player.save(update_fields=("session_expires_at", "updated_at"))

        self.game = Game.objects.create(
            room=self.room,
            status=GameStatus.IN_PROGRESS,
            game_mode=RoomGameMode.DUO,
        )
        self.game_word = GameWord.objects.create(game=self.game, text="apple")
        self.round = Round.objects.create(
            game=self.game,
            drawer_participant=self.primary_drawer,
            drawer_nickname=self.primary_drawer.display_name,
            second_drawer_participant=self.second_drawer,
            second_drawer_nickname=self.second_drawer.display_name,
            selected_game_word=self.game_word,
            sequence_number=1,
        )

        with patch("games.runtime._start_round_timer"), patch(
            "games.runtime.broadcast_room_event"
        ), patch("games.runtime.broadcast_player_event"):
            game_runtime.start_round_runtime(self.round.id)

    def tearDown(self):
        game_services._redis_client = self._original_services_redis_client
        game_runtime.reset_runtime_state_for_tests()

    def _create_player(self, session_key: str, display_name: str) -> Player:
        return Player.objects.create(
            room=self.room,
            session_key=session_key,
            display_name=display_name,
            connection_status=Player.ConnectionStatus.CONNECTED,
            participation_status=Player.ParticipationStatus.PLAYING,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )

    def _set_connected(self, player: Player, connected: bool) -> None:
        Player.objects.filter(pk=player.id).update(
            connection_status=(
                Player.ConnectionStatus.CONNECTED
                if connected
                else Player.ConnectionStatus.DISCONNECTED
            ),
            updated_at=timezone.now(),
        )

    def _disconnect_player(self, player: Player) -> None:
        self._set_connected(player, False)
        game_runtime.handle_participant_disconnected(
            join_code=self.room.join_code,
            participant_id=player.id,
        )

    def _reconnect_player(self, player: Player) -> None:
        self._set_connected(player, True)
        game_runtime.handle_participant_reconnected(
            join_code=self.room.join_code,
            participant_id=player.id,
        )

    def _turn_state(self) -> dict[str, str]:
        return game_redis.get_turn_state(self.fake_redis, self.room.join_code)

    def assertNoDrawerGrace(self) -> None:
        turn_state = self._turn_state()
        self.assertEqual(turn_state.get("status"), "drawing")
        self.assertFalse(turn_state.get("drawer_disconnect_deadline_at"))
        self.assertFalse(
            game_runtime.get_timer_status_for_tests(
                self.room.join_code,
            )["drawer_disconnect_timer_running"]
        )
        self.round.refresh_from_db()
        self.assertIsNone(self.round.status)
        self.assertIsNone(self.round.ended_at)

    def test_primary_drawer_disconnect_alone_keeps_duo_round_active_without_grace(self):
        self._disconnect_player(self.primary_drawer)

        self.assertNoDrawerGrace()

    def test_second_drawer_disconnect_alone_keeps_duo_round_active_without_grace(self):
        self._disconnect_player(self.second_drawer)

        self.assertNoDrawerGrace()

    def test_dual_drawer_disconnect_primary_then_second_starts_one_grace_timer(self):
        with patch("games.runtime._start_drawer_disconnect_timer") as start_timer:
            self._disconnect_player(self.primary_drawer)
            self.assertNoDrawerGrace()

            self._disconnect_player(self.second_drawer)

        turn_state = self._turn_state()
        self.assertEqual(turn_state.get("status"), "drawer_disconnected_grace")
        self.assertTrue(turn_state.get("drawer_disconnect_deadline_at"))
        start_timer.assert_called_once_with(
            join_code=self.room.join_code,
            round_id=self.round.id,
        )

    def test_dual_drawer_disconnect_second_then_primary_starts_one_grace_timer(self):
        with patch("games.runtime._start_drawer_disconnect_timer") as start_timer:
            self._disconnect_player(self.second_drawer)
            self.assertNoDrawerGrace()

            self._disconnect_player(self.primary_drawer)

        turn_state = self._turn_state()
        self.assertEqual(turn_state.get("status"), "drawer_disconnected_grace")
        self.assertTrue(turn_state.get("drawer_disconnect_deadline_at"))
        start_timer.assert_called_once_with(
            join_code=self.room.join_code,
            round_id=self.round.id,
        )

    def test_primary_reconnect_during_dual_disconnect_grace_clears_timer(self):
        with patch("games.runtime._start_drawer_disconnect_timer"):
            self._disconnect_player(self.primary_drawer)
            self._disconnect_player(self.second_drawer)

        self._reconnect_player(self.primary_drawer)

        self.assertNoDrawerGrace()

    def test_second_drawer_reconnect_during_dual_disconnect_grace_clears_timer(self):
        with patch("games.runtime._start_drawer_disconnect_timer"):
            self._disconnect_player(self.primary_drawer)
            self._disconnect_player(self.second_drawer)

        self._reconnect_player(self.second_drawer)

        self.assertNoDrawerGrace()

    def test_dual_disconnect_grace_expiry_ends_round_as_drawer_disconnected(self):
        self._set_connected(self.primary_drawer, False)
        self._set_connected(self.second_drawer, False)
        game_redis.update_turn_state_fields(
            self.fake_redis,
            self.room.join_code,
            {
                "status": "drawer_disconnected_grace",
                "drawer_disconnect_deadline_at": (
                    timezone.now() - timedelta(seconds=1)
                ).isoformat(),
            },
        )

        with patch("games.runtime.broadcast_room_event"), patch(
            "games.runtime._start_intermission_timer"
        ):
            game_runtime._drawer_disconnect_timer_worker(
                join_code=self.room.join_code,
                round_id=self.round.id,
                stop_event=threading.Event(),
            )

        self.round.refresh_from_db()
        self.assertEqual(self.round.status, RoundStatus.DRAWER_DISCONNECTED)
        self.assertIsNotNone(self.round.ended_at)

    def test_dual_disconnect_grace_expiry_does_not_end_if_either_drawer_returned(self):
        self._set_connected(self.primary_drawer, True)
        self._set_connected(self.second_drawer, False)
        game_redis.update_turn_state_fields(
            self.fake_redis,
            self.room.join_code,
            {
                "status": "drawer_disconnected_grace",
                "drawer_disconnect_deadline_at": (
                    timezone.now() - timedelta(seconds=1)
                ).isoformat(),
            },
        )

        with patch("games.runtime.broadcast_room_event"):
            game_runtime._drawer_disconnect_timer_worker(
                join_code=self.room.join_code,
                round_id=self.round.id,
                stop_event=threading.Event(),
            )

        self.round.refresh_from_db()
        self.assertIsNone(self.round.status)
        self.assertIsNone(self.round.ended_at)
        self.assertNoDrawerGrace()

    def test_single_drawer_round_disconnect_still_starts_grace(self):
        self.round.second_drawer_participant = None
        self.round.second_drawer_nickname = None
        self.round.game.game_mode = RoomGameMode.NORMAL
        self.round.game.save(update_fields=("game_mode", "updated_at"))
        self.round.save(
            update_fields=(
                "second_drawer_participant",
                "second_drawer_nickname",
                "updated_at",
            )
        )
        game_redis.update_turn_state_fields(
            self.fake_redis,
            self.room.join_code,
            {"second_drawer_participant_id": ""},
        )

        with patch("games.runtime._start_drawer_disconnect_timer") as start_timer:
            self._disconnect_player(self.primary_drawer)

        turn_state = self._turn_state()
        self.assertEqual(turn_state.get("status"), "drawer_disconnected_grace")
        self.assertTrue(turn_state.get("drawer_disconnect_deadline_at"))
        start_timer.assert_called_once_with(
            join_code=self.room.join_code,
            round_id=self.round.id,
        )

    def test_single_drawer_continuation_preserves_d06_split_scoring(self):
        self._disconnect_player(self.second_drawer)
        accepted_at = self.round.started_at + timedelta(seconds=45)

        with patch("games.services.timezone.now", return_value=accepted_at):
            result = game_services.evaluate_guess_for_round(
                self.round,
                self.guesser,
                self.game_word.text,
            )

        self.assertTrue(result.is_correct)
        self.primary_drawer.refresh_from_db()
        self.second_drawer.refresh_from_db()
        self.guesser.refresh_from_db()
        self.assertEqual(self.guesser.current_score, 60)
        self.assertEqual(self.primary_drawer.current_score, 15)
        self.assertEqual(self.second_drawer.current_score, 15)
