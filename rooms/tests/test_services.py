"""Tests for room-domain participant lifecycle services."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import fakeredis
from django.test import TestCase
from django.utils import timezone

from games import redis as game_redis
from games.models import Game, GameStatus, GameWord, Round, RoundStatus
from rooms import redis as room_redis
from rooms.models import Player, Room
from rooms.services import (
    cleanup_expired_empty_rooms,
    connect_participant,
    delete_room_if_empty_grace_expired,
    disconnect_participant,
    enter_empty_room_grace,
    get_empty_room_cleanup_deadline,
    leave_participant,
    promote_mid_game_spectators_to_players,
    purge_expired_participants,
    purge_expired_participants_for_session,
    restore_room_from_empty_grace,
)


class ParticipantLifecycleServiceTests(TestCase):
    """Service-level coverage for participant connection lifecycle rules."""

    def _execute_on_commit(self, callback):
        """Run code and immediately execute any on-commit callbacks it registers."""

        with self.captureOnCommitCallbacks(execute=True):
            callback()

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

        self._execute_on_commit(
            lambda: leave_participant(
                redis_client=self.redis_client,
                player_id=self.player.id,
            )
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

    def test_last_permanent_leave_cancels_active_game_before_entering_empty_grace(self):
        second_player = Player.objects.create(
            room=self.room,
            session_key="session-456",
            display_name="Jamie",
            connection_status=Player.ConnectionStatus.CONNECTED,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )
        self.room.status = Room.Status.IN_PROGRESS
        self.room.save(update_fields=["status", "updated_at"])

        active_game = Game.objects.create(
            room=self.room,
            status=GameStatus.IN_PROGRESS,
        )
        game_word = GameWord.objects.create(game=active_game, text="rocket")
        active_round = Round.objects.create(
            game=active_game,
            drawer_participant=self.player,
            drawer_nickname=self.player.display_name,
            selected_game_word=game_word,
            sequence_number=1,
        )

        self._execute_on_commit(
            lambda: leave_participant(
                redis_client=self.redis_client,
                player_id=second_player.id,
            )
        )
        self._execute_on_commit(
            lambda: leave_participant(
                redis_client=self.redis_client,
                player_id=self.player.id,
            )
        )

        self.room.refresh_from_db()
        active_game.refresh_from_db()
        active_round.refresh_from_db()

        self.assertEqual(self.room.status, Room.Status.EMPTY_GRACE)
        self.assertIsNotNone(self.room.empty_since)
        self.assertEqual(active_game.status, GameStatus.CANCELLED)
        self.assertIsNotNone(active_game.ended_at)
        self.assertEqual(active_round.status, RoundStatus.CANCELLED)
        self.assertIsNotNone(active_round.ended_at)

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

    def _execute_on_commit(self, callback):
        """Run code and immediately execute any on-commit callbacks it registers."""

        with self.captureOnCommitCallbacks(execute=True):
            callback()

    def setUp(self):
        self.redis_client = fakeredis.FakeRedis()
        self.room = Room.objects.create(
            name="Grace Room",
            join_code="GRACE123",
            visibility=Room.Visibility.PRIVATE,
        )

    def test_enter_empty_room_grace_sets_status_timestamp_and_cleanup_deadline(self):
        entered_at = timezone.now()

        self._execute_on_commit(
            lambda: enter_empty_room_grace(
                redis_client=self.redis_client,
                room_id=self.room.id,
                now=entered_at,
            )
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

        self._execute_on_commit(
            lambda: enter_empty_room_grace(
                redis_client=self.redis_client,
                room_id=self.room.id,
                now=timezone.now(),
            )
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
        self._execute_on_commit(
            lambda: enter_empty_room_grace(
                redis_client=self.redis_client,
                room_id=self.room.id,
                now=timezone.now(),
            )
        )

        self._execute_on_commit(
            lambda: restore_room_from_empty_grace(
                redis_client=self.redis_client,
                room_id=self.room.id,
            )
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

    def test_restore_room_from_empty_grace_cancels_active_game_and_clears_runtime(self):
        game = Game.objects.create(
            room=self.room,
            status=GameStatus.IN_PROGRESS,
        )
        game_word = GameWord.objects.create(
            game=game,
            text="rocket",
        )
        active_round = Round.objects.create(
            game=game,
            drawer_nickname="Drawer",
            selected_game_word=game_word,
            sequence_number=1,
        )
        entered_at = timezone.now()
        self.room.status = Room.Status.EMPTY_GRACE
        self.room.empty_since = entered_at
        self.room.save(update_fields=["status", "empty_since", "updated_at"])
        game_redis.set_turn_state(
            self.redis_client,
            self.room.join_code,
            {
                "phase": "round",
                "status": "drawing",
                "game_id": str(game.id),
                "round_id": str(active_round.id),
                "deadline_at": (entered_at + timedelta(minutes=1)).isoformat(),
            },
        )
        game_redis.set_drawer_pool(self.redis_client, self.room.join_code, [1, 2])
        game_redis.set_round_payloads(
            self.redis_client,
            self.room.join_code,
            {"word": "rocket"},
            {"mask": "r_____"},
        )
        game_redis.set_guess_state(
            self.redis_client,
            self.room.join_code,
            active_round.id,
            99,
            {"status": "correct"},
        )
        game_redis.set_deadline(
            self.redis_client,
            self.room.join_code,
            "round_end",
            (entered_at + timedelta(minutes=1)).isoformat(),
        )
        game_redis.set_deadline(
            self.redis_client,
            self.room.join_code,
            "cleanup",
            get_empty_room_cleanup_deadline(empty_since=entered_at).isoformat(),
        )

        self._execute_on_commit(
            lambda: restore_room_from_empty_grace(
                redis_client=self.redis_client,
                room_id=self.room.id,
            )
        )

        self.room.refresh_from_db()
        game.refresh_from_db()
        active_round.refresh_from_db()

        self.assertEqual(self.room.status, Room.Status.LOBBY)
        self.assertIsNone(self.room.empty_since)
        self.assertEqual(game.status, GameStatus.CANCELLED)
        self.assertIsNotNone(game.ended_at)
        self.assertEqual(active_round.status, RoundStatus.CANCELLED)
        self.assertIsNotNone(active_round.ended_at)
        self.assertEqual(game_redis.get_turn_state(self.redis_client, self.room.join_code), {})
        self.assertEqual(game_redis.get_drawer_pool(self.redis_client, self.room.join_code), set())
        self.assertIsNone(
            game_redis.get_round_payload(self.redis_client, self.room.join_code, "drawer")
        )
        self.assertIsNone(
            game_redis.get_guess_state(
                self.redis_client,
                self.room.join_code,
                active_round.id,
                99,
            )
        )
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
        self._execute_on_commit(
            lambda: enter_empty_room_grace(
                redis_client=self.redis_client,
                room_id=self.room.id,
                now=entered_at,
            )
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
        self._execute_on_commit(
            lambda: enter_empty_room_grace(
                redis_client=self.redis_client,
                room_id=self.room.id,
                now=entered_at,
            )
        )

        deleted: list[bool] = []
        self._execute_on_commit(
            lambda: deleted.append(
                delete_room_if_empty_grace_expired(
                    redis_client=self.redis_client,
                    room_id=self.room.id,
                    now=entered_at + timedelta(minutes=10),
                )
            )
        )
        deleted_result = deleted[0]

        self.assertTrue(deleted_result)
        self.assertFalse(Room.objects.filter(pk=self.room.id).exists())
        self.assertIsNone(
            game_redis.get_deadline(
                self.redis_client,
                "GRACE123",
                "cleanup",
            )
        )

    def test_delete_room_if_empty_grace_expired_clears_game_runtime_state(self):
        entered_at = timezone.now() - timedelta(minutes=10, seconds=1)
        game = Game.objects.create(
            room=self.room,
            status=GameStatus.IN_PROGRESS,
        )
        game_word = GameWord.objects.create(game=game, text="planet")
        active_round = Round.objects.create(
            game=game,
            drawer_nickname="Drawer",
            selected_game_word=game_word,
            sequence_number=1,
        )
        self.room.status = Room.Status.EMPTY_GRACE
        self.room.empty_since = entered_at
        self.room.save(update_fields=["status", "empty_since", "updated_at"])
        game_redis.set_turn_state(
            self.redis_client,
            self.room.join_code,
            {
                "phase": "round",
                "status": "drawing",
                "game_id": str(game.id),
                "round_id": str(active_round.id),
                "deadline_at": (entered_at + timedelta(minutes=1)).isoformat(),
            },
        )
        game_redis.set_drawer_pool(self.redis_client, self.room.join_code, [1, 2])
        game_redis.set_round_payloads(
            self.redis_client,
            self.room.join_code,
            {"word": "planet"},
            {"mask": "p_____"},
        )
        game_redis.set_guess_state(
            self.redis_client,
            self.room.join_code,
            active_round.id,
            7,
            {"status": "incorrect"},
        )
        game_redis.set_deadline(
            self.redis_client,
            self.room.join_code,
            "round_end",
            (entered_at + timedelta(minutes=1)).isoformat(),
        )
        game_redis.set_deadline(
            self.redis_client,
            self.room.join_code,
            "cleanup",
            get_empty_room_cleanup_deadline(empty_since=entered_at).isoformat(),
        )

        deleted: list[bool] = []
        self._execute_on_commit(
            lambda: deleted.append(
                delete_room_if_empty_grace_expired(
                    redis_client=self.redis_client,
                    room_id=self.room.id,
                    now=timezone.now(),
                )
            )
        )
        deleted_result = deleted[0]

        self.assertTrue(deleted_result)
        self.assertFalse(Room.objects.filter(pk=self.room.id).exists())
        self.assertEqual(game_redis.get_turn_state(self.redis_client, self.room.join_code), {})
        self.assertEqual(game_redis.get_drawer_pool(self.redis_client, self.room.join_code), set())
        self.assertIsNone(
            game_redis.get_round_payload(self.redis_client, self.room.join_code, "drawer")
        )
        self.assertIsNone(
            game_redis.get_guess_state(
                self.redis_client,
                self.room.join_code,
                active_round.id,
                7,
            )
        )
        self.assertIsNone(
            game_redis.get_deadline(
                self.redis_client,
                self.room.join_code,
                "cleanup",
            )
        )

    def test_cleanup_expired_empty_rooms_deletes_only_expired_candidates(self):
        now = timezone.now()
        expired_room = Room.objects.create(
            name="Expired Grace Room",
            join_code="EXPIR123",
            visibility=Room.Visibility.PRIVATE,
            status=Room.Status.EMPTY_GRACE,
            empty_since=now - timedelta(minutes=10, seconds=1),
        )
        fresh_room = Room.objects.create(
            name="Fresh Grace Room",
            join_code="FRESH123",
            visibility=Room.Visibility.PRIVATE,
            status=Room.Status.EMPTY_GRACE,
            empty_since=now - timedelta(minutes=2),
        )
        occupied_room = Room.objects.create(
            name="Occupied Grace Room",
            join_code="BUSY1234",
            visibility=Room.Visibility.PRIVATE,
            status=Room.Status.EMPTY_GRACE,
            empty_since=now - timedelta(minutes=12),
        )
        Player.objects.create(
            room=occupied_room,
            session_key="occupied-session",
            display_name="Occupied",
            connection_status=Player.ConnectionStatus.DISCONNECTED,
            session_expires_at=now + timedelta(hours=1),
        )

        expired_deadline = get_empty_room_cleanup_deadline(
            empty_since=expired_room.empty_since,
        ).isoformat()
        fresh_deadline = get_empty_room_cleanup_deadline(
            empty_since=fresh_room.empty_since,
        ).isoformat()
        occupied_deadline = get_empty_room_cleanup_deadline(
            empty_since=occupied_room.empty_since,
        ).isoformat()
        game_redis.set_deadline(
            self.redis_client,
            expired_room.join_code,
            "cleanup",
            expired_deadline,
        )
        game_redis.set_deadline(
            self.redis_client,
            fresh_room.join_code,
            "cleanup",
            fresh_deadline,
        )
        game_redis.set_deadline(
            self.redis_client,
            occupied_room.join_code,
            "cleanup",
            occupied_deadline,
        )

        deleted_counts: list[int] = []
        self._execute_on_commit(
            lambda: deleted_counts.append(
                cleanup_expired_empty_rooms(
                    redis_client=self.redis_client,
                    now=now,
                )
            )
        )
        deleted_count = deleted_counts[0]

        self.assertEqual(deleted_count, 1)
        self.assertFalse(Room.objects.filter(pk=expired_room.id).exists())
        self.assertTrue(Room.objects.filter(pk=fresh_room.id).exists())
        self.assertTrue(Room.objects.filter(pk=occupied_room.id).exists())
        self.assertIsNone(
            game_redis.get_deadline(
                self.redis_client,
                expired_room.join_code,
                "cleanup",
            )
        )
        self.assertEqual(
            game_redis.get_deadline(
                self.redis_client,
                fresh_room.join_code,
                "cleanup",
            ),
            fresh_deadline,
        )
        self.assertEqual(
            game_redis.get_deadline(
                self.redis_client,
                occupied_room.join_code,
                "cleanup",
            ),
            occupied_deadline,
        )

    def test_purge_expired_participants_routes_last_expired_member_into_empty_grace(self):
        expired_at = timezone.now() - timedelta(minutes=1)
        expired_player = Player.objects.create(
            room=self.room,
            session_key="expired-session",
            display_name="Expired Alex",
            connection_status=Player.ConnectionStatus.DISCONNECTED,
            session_expires_at=expired_at,
        )
        self.room.host = expired_player
        self.room.save(update_fields=["host", "updated_at"])

        purged_counts: list[int] = []
        self._execute_on_commit(
            lambda: purged_counts.append(
                purge_expired_participants(
                    redis_client=self.redis_client,
                    now=timezone.now(),
                )
            )
        )
        purged_count = purged_counts[0]

        self.room.refresh_from_db()
        self.assertEqual(purged_count, 1)
        self.assertFalse(Player.objects.filter(pk=expired_player.id).exists())
        self.assertEqual(self.room.status, Room.Status.EMPTY_GRACE)
        self.assertIsNotNone(self.room.empty_since)
        self.assertIsNotNone(
            game_redis.get_deadline(
                self.redis_client,
                self.room.join_code,
                "cleanup",
            )
        )

    def test_purge_expired_participants_keeps_unexpired_member(self):
        active_player = Player.objects.create(
            room=self.room,
            session_key="active-session",
            display_name="Active Alex",
            connection_status=Player.ConnectionStatus.DISCONNECTED,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )

        purged_count = purge_expired_participants(
            redis_client=self.redis_client,
            now=timezone.now(),
        )

        self.room.refresh_from_db()
        self.assertEqual(purged_count, 0)
        self.assertTrue(Player.objects.filter(pk=active_player.id).exists())
        self.assertEqual(self.room.status, Room.Status.LOBBY)

    def test_purge_expired_participants_for_session_only_removes_matching_session(self):
        expired_at = timezone.now() - timedelta(minutes=1)
        target_player = Player.objects.create(
            room=self.room,
            session_key="target-session",
            display_name="Expired Alex",
            connection_status=Player.ConnectionStatus.DISCONNECTED,
            session_expires_at=expired_at,
        )
        self.room.host = target_player
        self.room.save(update_fields=["host", "updated_at"])
        other_room = Room.objects.create(
            name="Other Room",
            join_code="OTHER123",
            visibility=Room.Visibility.PRIVATE,
        )
        other_player = Player.objects.create(
            room=other_room,
            session_key="other-session",
            display_name="Expired Jamie",
            connection_status=Player.ConnectionStatus.DISCONNECTED,
            session_expires_at=expired_at,
        )
        other_room.host = other_player
        other_room.save(update_fields=["host", "updated_at"])

        purged_counts: list[int] = []
        self._execute_on_commit(
            lambda: purged_counts.append(
                purge_expired_participants_for_session(
                    redis_client=self.redis_client,
                    session_key="target-session",
                    now=timezone.now(),
                )
            )
        )
        purged_count = purged_counts[0]

        self.room.refresh_from_db()
        other_room.refresh_from_db()
        self.assertEqual(purged_count, 1)
        self.assertFalse(Player.objects.filter(pk=target_player.id).exists())
        self.assertTrue(Player.objects.filter(pk=other_player.id).exists())
        self.assertEqual(self.room.status, Room.Status.EMPTY_GRACE)
        self.assertEqual(other_room.status, Room.Status.LOBBY)
        self.assertEqual(other_room.host_id, other_player.id)


class PromoteMidGameSpectatorsServiceTests(TestCase):
    """Service-level coverage for the mid-game spectator promotion function."""

    def setUp(self):
        self.redis_client = fakeredis.FakeRedis()
        self.room = Room.objects.create(
            name="Promotion Room",
            join_code="PROMO123",
            visibility=Room.Visibility.PRIVATE,
            status=Room.Status.IN_PROGRESS,
        )

    def _make_player(self, session_key, display_name, participation_status):
        """Helper that creates a participant with the given participation status."""
        return Player.objects.create(
            room=self.room,
            session_key=session_key,
            display_name=display_name,
            participation_status=participation_status,
            connection_status=Player.ConnectionStatus.CONNECTED,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )

    def test_promote_mid_game_spectators_promotes_all_spectating_participants(self):
        # Two spectators who joined mid-game should both be promoted to PLAYING.
        spectator_a = self._make_player(
            "session-spec-a", "Spectator A", Player.ParticipationStatus.SPECTATING
        )
        spectator_b = self._make_player(
            "session-spec-b", "Spectator B", Player.ParticipationStatus.SPECTATING
        )

        promote_mid_game_spectators_to_players(room_id=self.room.id)

        spectator_a.refresh_from_db()
        spectator_b.refresh_from_db()
        self.assertEqual(spectator_a.participation_status, Player.ParticipationStatus.PLAYING)
        self.assertEqual(spectator_b.participation_status, Player.ParticipationStatus.PLAYING)

    def test_promote_mid_game_spectators_leaves_playing_participants_unchanged(self):
        # Participants already marked PLAYING must not be touched by the promotion.
        playing_player = self._make_player(
            "session-playing", "Full Player", Player.ParticipationStatus.PLAYING
        )
        spectator = self._make_player(
            "session-spec", "Spectator", Player.ParticipationStatus.SPECTATING
        )

        promote_mid_game_spectators_to_players(room_id=self.room.id)

        playing_player.refresh_from_db()
        spectator.refresh_from_db()
        # The already-playing participant's status is unchanged.
        self.assertEqual(playing_player.participation_status, Player.ParticipationStatus.PLAYING)
        # The spectator is now promoted.
        self.assertEqual(spectator.participation_status, Player.ParticipationStatus.PLAYING)

    def test_promote_mid_game_spectators_returns_count_of_promoted_participants(self):
        # The return value lets callers (e.g. game services) know how many
        # promotions happened without issuing a second query.
        self._make_player(
            "session-spec-a", "Spectator A", Player.ParticipationStatus.SPECTATING
        )
        self._make_player(
            "session-spec-b", "Spectator B", Player.ParticipationStatus.SPECTATING
        )
        self._make_player(
            "session-playing", "Full Player", Player.ParticipationStatus.PLAYING
        )

        promoted_count = promote_mid_game_spectators_to_players(room_id=self.room.id)

        # Only the two spectators should be counted; the playing participant is skipped.
        self.assertEqual(promoted_count, 2)

    def test_promote_mid_game_spectators_returns_zero_when_no_spectators_present(self):
        # Rooms with no spectators (typical mid-round state) must return 0
        # without raising an error — the caller at round transition calls this
        # unconditionally, so a no-op path must be safe.
        self._make_player(
            "session-playing", "Full Player", Player.ParticipationStatus.PLAYING
        )

        promoted_count = promote_mid_game_spectators_to_players(room_id=self.room.id)

        self.assertEqual(promoted_count, 0)

    def test_promote_mid_game_spectators_only_affects_the_target_room(self):
        # Spectators in a different room must not be promoted when this room's
        # transition fires, otherwise a bulk UPDATE without a room filter would
        # corrupt every room's state simultaneously.
        other_room = Room.objects.create(
            name="Other Room",
            join_code="OTHER123",
            visibility=Room.Visibility.PRIVATE,
            status=Room.Status.IN_PROGRESS,
        )
        spectator_in_target = self._make_player(
            "session-target-spec", "Target Spectator", Player.ParticipationStatus.SPECTATING
        )
        spectator_in_other = Player.objects.create(
            room=other_room,
            session_key="session-other-spec",
            display_name="Other Spectator",
            participation_status=Player.ParticipationStatus.SPECTATING,
            connection_status=Player.ConnectionStatus.CONNECTED,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )

        promote_mid_game_spectators_to_players(room_id=self.room.id)

        spectator_in_target.refresh_from_db()
        spectator_in_other.refresh_from_db()
        # Only the spectator in the target room is promoted.
        self.assertEqual(spectator_in_target.participation_status, Player.ParticipationStatus.PLAYING)
        # The other room's spectator is untouched.
        self.assertEqual(spectator_in_other.participation_status, Player.ParticipationStatus.SPECTATING)

    def test_promote_mid_game_spectators_ignores_disconnected_spectators(self):
        # A-07 safeguard: a DISCONNECTED spectator (e.g. a player who did an
        # HTTP join but never opened a socket, or who dropped their
        # connection while spectating) must NOT be silently promoted to
        # PLAYING while they are offline. Promoting them would create a
        # "ghost PLAYING" state where downstream code that combines
        # participation_status with connection_status still filters them
        # out but their stored status misrepresents their role. Keeping
        # them in SPECTATING means they will be promoted naturally on the
        # next round transition after they reconnect.
        disconnected_spectator = Player.objects.create(
            room=self.room,
            session_key="session-disconnected-spec",
            display_name="Disconnected Spectator",
            participation_status=Player.ParticipationStatus.SPECTATING,
            connection_status=Player.ConnectionStatus.DISCONNECTED,
            session_expires_at=timezone.now() + timedelta(hours=1),
        )
        connected_spectator = self._make_player(
            "session-connected-spec",
            "Connected Spectator",
            Player.ParticipationStatus.SPECTATING,
        )

        promoted_count = promote_mid_game_spectators_to_players(room_id=self.room.id)

        disconnected_spectator.refresh_from_db()
        connected_spectator.refresh_from_db()
        # Only the connected spectator is promoted.
        self.assertEqual(promoted_count, 1)
        self.assertEqual(
            connected_spectator.participation_status,
            Player.ParticipationStatus.PLAYING,
        )
        # The disconnected spectator stays SPECTATING.
        self.assertEqual(
            disconnected_spectator.participation_status,
            Player.ParticipationStatus.SPECTATING,
        )
