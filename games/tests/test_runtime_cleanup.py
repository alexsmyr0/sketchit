"""Tests for room-wide gameplay runtime teardown."""

from __future__ import annotations

import threading

import fakeredis
from django.test import SimpleTestCase

from games import redis as game_redis
from games import runtime as game_runtime


class RoomRuntimeTeardownTests(SimpleTestCase):
    """Verify room runtime teardown clears state and stops local workers."""

    def setUp(self):
        game_runtime.reset_runtime_state_for_tests()
        self.redis_client = fakeredis.FakeRedis()
        game_runtime._redis_client = self.redis_client

    def tearDown(self):
        game_runtime.reset_runtime_state_for_tests()
        super().tearDown()

    def test_teardown_room_runtime_stops_threads_and_clears_room_keys(self):
        join_code = "RUNT1234"
        stop_round = threading.Event()
        stop_intermission = threading.Event()
        round_thread = threading.Thread(
            target=stop_round.wait,
            args=(5,),
            daemon=True,
        )
        intermission_thread = threading.Thread(
            target=stop_intermission.wait,
            args=(5,),
            daemon=True,
        )
        round_thread.start()
        intermission_thread.start()

        with game_runtime._room_timer_lock:
            game_runtime._room_timer_handles_by_join_code[join_code] = (
                game_runtime._RoomTimerHandles(
                    active_round_id=5,
                    round_stop_event=stop_round,
                    round_thread=round_thread,
                    intermission_stop_event=stop_intermission,
                    intermission_thread=intermission_thread,
                )
            )

        game_redis.set_turn_state(
            self.redis_client,
            join_code,
            {
                "phase": "round",
                "status": "drawing",
                "game_id": "1",
                "round_id": "5",
                "deadline_at": "2030-01-01T00:00:00+00:00",
            },
        )
        game_redis.set_drawer_pool(self.redis_client, join_code, [1, 2, 3])
        game_redis.set_round_payloads(
            self.redis_client,
            join_code,
            {"word": "rocket"},
            {"mask": "r_____"},
        )
        game_redis.set_guess_state(
            self.redis_client,
            join_code,
            5,
            42,
            {"status": "correct"},
        )
        game_redis.set_deadline(
            self.redis_client,
            join_code,
            "round_end",
            "2030-01-01T00:00:00+00:00",
        )
        game_redis.set_deadline(
            self.redis_client,
            join_code,
            "intermission_end",
            "2030-01-01T00:00:05+00:00",
        )
        game_redis.set_deadline(
            self.redis_client,
            join_code,
            "cleanup",
            "2030-01-01T00:10:00+00:00",
        )

        game_runtime.teardown_room_runtime(
            join_code,
            include_cleanup_deadline=True,
        )

        self.assertTrue(stop_round.is_set())
        self.assertTrue(stop_intermission.is_set())
        self.assertFalse(round_thread.is_alive())
        self.assertFalse(intermission_thread.is_alive())
        self.assertEqual(
            game_runtime.get_timer_status_for_tests(join_code),
            {
                "round_timer_running": False,
                "intermission_timer_running": False,
            },
        )
        self.assertEqual(game_redis.get_turn_state(self.redis_client, join_code), {})
        self.assertEqual(game_redis.get_drawer_pool(self.redis_client, join_code), set())
        self.assertIsNone(game_redis.get_round_payload(self.redis_client, join_code, "drawer"))
        self.assertIsNone(game_redis.get_guess_state(self.redis_client, join_code, 5, 42))
        self.assertIsNone(game_redis.get_deadline(self.redis_client, join_code, "round_end"))
        self.assertIsNone(
            game_redis.get_deadline(self.redis_client, join_code, "intermission_end")
        )
        self.assertIsNone(game_redis.get_deadline(self.redis_client, join_code, "cleanup"))
