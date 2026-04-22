"""
Tests for drawing event broadcasting and snapshot synchronization.

These tests verify that only the active drawer can broadcast drawing events
and that the server correctly manages the canvas snapshot in Redis for
reconnect recovery.
"""

import json
import fakeredis
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings

from asgiref.sync import async_to_sync
from rooms.models import Player, Room
from games.models import Game, GameStatus, GameWord, Round
from rooms.tests.test_consumers import (
    _ws_url, _session_headers, _create_room_member, _TEST_APP,
    _receive_until_type, _connect_and_receive_initial_room_state,
    _connect_and_drain_initial_sync, _drain_output_queue_nowait
)
from rooms import consumers as room_consumers
from rooms import redis as room_redis
from games import redis as game_redis
from games import services as game_services
from words.models import Word, WordPack, WordPackEntry


@override_settings(SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True)
class DrawingEventTests(TransactionTestCase):
    """Tests for drawer authorization and event broadcasting."""

    def setUp(self):
        from rooms import consumers as room_consumers
        from games import runtime as game_runtime
        from games import services as game_services
        from rooms import services as room_services
        
        game_runtime.reset_runtime_state_for_tests()
        room_consumers.reset_redis_client()
        
        self.fake_redis = fakeredis.FakeRedis()
        room_consumers._redis_client = self.fake_redis
        game_runtime._redis_client = self.fake_redis
        
        game_services._get_redis_client = lambda: self.fake_redis
        room_services._get_redis_client = lambda: self.fake_redis

        # Set up a word pack for the room
        self.word_pack = WordPack.objects.create(name="Test Pack")
        test_word = Word.objects.create(text="rocket")
        WordPackEntry.objects.create(word_pack=self.word_pack, word=test_word)

        self.room = Room.objects.create(
            name="Drawing Room",
            join_code="DRAW1234",
            status=Room.Status.IN_PROGRESS,  # Active game needed for drawing
            word_pack=self.word_pack,
        )
        
        # Create two participants with proper synchronization
        self.drawer_session_key = async_to_sync(_create_room_member)(self.room.id, "Drawer")
        self.drawer_player = Player.objects.get(room=self.room, session_key=self.drawer_session_key)
        
        self.viewer_session_key = async_to_sync(_create_room_member)(self.room.id, "Viewer")
        self.viewer_player = Player.objects.get(room=self.room, session_key=self.viewer_session_key)

        # Create mandatory Game and Round objects for runtime sync
        from games.models import Game, GameStatus, GameWord, Round
        self.game = Game.objects.create(room=self.room, status=GameStatus.IN_PROGRESS)
        self.game_word = GameWord.objects.create(game=self.game, text="testword")
        self.round = Round.objects.create(
            game=self.game,
            drawer_participant=self.drawer_player,
            drawer_nickname=self.drawer_player.display_name,
            selected_game_word=self.game_word,
            sequence_number=1,
        )

    def _seed_active_round_state(self):
        from games import redis as game_redis
        from django.utils import timezone
        from datetime import timedelta
        state = {
            "phase": "round",
            "round_id": str(self.round.id),
            "game_id": str(self.game.id),
            "drawer_participant_id": str(self.drawer_player.id),
            "deadline_at": (timezone.now() + timedelta(seconds=60)).isoformat(),
        }
        game_redis.set_turn_state(self.fake_redis, self.room.join_code, state)

    def tearDown(self):
        room_consumers.reset_redis_client()
        super().tearDown()

    async def test_drawer_can_broadcast_stroke(self):
        self._seed_active_round_state()
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code, expects_game_active=True)
        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)

        stroke_data = {"lines": [[0,0], [10,10]], "color": "blue"}
        await drawer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": stroke_data
        })
        
        # Viewer should receive the broadcast
        response = await _receive_until_type(viewer_socket, "drawing.stroke")
        self.assertEqual(response["payload"], stroke_data)

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_viewer_receives_stroke(self):
        self._seed_active_round_state()
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code, expects_game_active=True)
        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)

        stroke_data = {"lines": [[0,0], [10,10]], "color": "red"}
        await drawer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": stroke_data
        })

        # Viewer should receive the broadcast
        response = await _receive_until_type(viewer_socket, "drawing.stroke")
        self.assertEqual(response["payload"], stroke_data)

        # Drawer should NOT receive their own broadcast
        _drain_output_queue_nowait(drawer_socket)
        self.assertTrue(await drawer_socket.receive_nothing())

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_drawing_end_stroke_broadcast(self):
        self._seed_active_round_state()
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code, expects_game_active=True)
        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)
        # Drawer sends an end stroke
        await drawer_socket.send_json_to({
            "type": "drawing.end_stroke",
            "payload": {}
        })

        # Viewer should receive the broadcast
        response = await _receive_until_type(viewer_socket, "drawing.end_stroke")

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_multiple_viewers_receive_broadcast(self):
        self._seed_active_round_state()
        # Create a second viewer
        second_viewer_key = await _create_room_member(self.room.id, "Viewer 2")
        
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        viewer1_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        viewer2_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(second_viewer_key),
        )
        
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code, expects_game_active=True)
        await _connect_and_drain_initial_sync(viewer1_socket, self.room.join_code, expects_game_active=True)
        await _connect_and_drain_initial_sync(viewer2_socket, self.room.join_code, expects_game_active=True)

        stroke_data = {"lines": [[0,0], [10,10]]}
        await drawer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": stroke_data
        })

        # Both viewers should receive the broadcast
        resp1 = await _receive_until_type(viewer1_socket, "drawing.stroke")
        resp2 = await _receive_until_type(viewer2_socket, "drawing.stroke")
        
        self.assertEqual(resp1["payload"], stroke_data)
        self.assertEqual(resp2["payload"], stroke_data)

        await drawer_socket.disconnect()
        await viewer1_socket.disconnect()
        await viewer2_socket.disconnect()

    async def test_non_drawer_cannot_broadcast_stroke(self):
        self._seed_active_round_state()
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code, expects_game_active=True)
        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)

        # Viewer (non-drawer) tries to send a stroke
        await viewer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": {"naughty": "secret"}
        })

        # Drawer should receive nothing
        _drain_output_queue_nowait(drawer_socket)
        self.assertTrue(await drawer_socket.receive_nothing())

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_drawing_clear_deletes_redis_snapshot(self):
        self._seed_active_round_state()
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code, expects_game_active=True)

        # Pre-seed a snapshot in Redis (list of JSON strings)
        stroke_data = {"type": "drawing.stroke", "payload": {"data": "seeded"}}
        room_redis.append_canvas_stroke(self.fake_redis, self.room.join_code, json.dumps(stroke_data).encode())

        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        # Replayed stroke will be drained by the handshake helper
        messages = await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)
        self.assertTrue(any(m.get("type") == "drawing.stroke" for m in messages))

        # Drawer clears the canvas
        await drawer_socket.send_json_to({"type": "drawing.clear"})
        
        # Viewer should receive the clear event broadcast
        # Room.state arrival broadcasts were drained by handshake, so clear should be next
        response = await _receive_until_type(viewer_socket, "drawing.clear")

        # Snapshot should be gone from Redis
        self.assertEqual(len(room_redis.get_canvas_snapshot(self.fake_redis, self.room.join_code)), 0)

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_drawing_not_allowed_in_lobby(self):
        # Move room back to lobby
        from games import redis as game_redis
        game_redis.clear_turn_state(self.fake_redis, self.room.join_code)
        
        from channels.db import database_sync_to_async
        self.room.status = Room.Status.LOBBY
        await database_sync_to_async(self.room.save)()

        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code, expects_game_active=False)
        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=False)

        # "Drawer" tries to draw in lobby
        await drawer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": {"wont": "work"}
        })

        # Viewer should receive nothing
                # Viewer should receive nothing
        self.assertTrue(await viewer_socket.receive_nothing())

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()


@override_settings(SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True)
class SnapshotSyncTests(TransactionTestCase):
    """Tests for canvas snapshot recovery on connection."""

    def setUp(self):
        from rooms import consumers as room_consumers
        from games import runtime as game_runtime
        from games import services as game_services
        from rooms import services as room_services

        game_runtime.reset_runtime_state_for_tests()
        room_consumers.reset_redis_client()

        self.fake_redis = fakeredis.FakeRedis()
        room_consumers._redis_client = self.fake_redis
        game_runtime._redis_client = self.fake_redis

        game_services._get_redis_client = lambda: self.fake_redis
        room_services._get_redis_client = lambda: self.fake_redis

        # Set up a word pack for the room
        self.word_pack = WordPack.objects.create(name="Snapshot Pack")
        test_word = Word.objects.create(text="rocket")
        WordPackEntry.objects.create(word_pack=self.word_pack, word=test_word)

        self.room = Room.objects.create(
            name="Snapshot Room",
            join_code="SNAP1234",
            status=Room.Status.IN_PROGRESS,
            word_pack=self.word_pack,
        )
        
        # Create participants using async_to_sync
        self.drawer_session_key = async_to_sync(_create_room_member)(self.room.id, "Drawer")
        self.drawer_player = Player.objects.get(room=self.room, session_key=self.drawer_session_key)
        
        self.viewer_session_key = async_to_sync(_create_room_member)(self.room.id, "Viewer")
        self.viewer_player = Player.objects.get(room=self.room, session_key=self.viewer_session_key)
        
        from games.models import Game, GameStatus, GameWord, Round
        self.game = Game.objects.create(room=self.room, status=GameStatus.IN_PROGRESS)
        self.game_word = GameWord.objects.create(game=self.game, text="testword")
        self.round = Round.objects.create(
            game=self.game,
            drawer_participant=self.drawer_player,
            drawer_nickname=self.drawer_player.display_name,
            selected_game_word=self.game_word,
            sequence_number=1,
        )

    def _seed_active_round_state(self):
        from games import redis as game_redis
        from django.utils import timezone
        from datetime import timedelta
        state = {
            "phase": "round",
            "round_id": str(self.round.id),
            "game_id": str(self.game.id),
            "drawer_participant_id": str(self.drawer_player.id),
            "deadline_at": (timezone.now() + timedelta(seconds=60)).isoformat(),
        }
        game_redis.set_turn_state(self.fake_redis, self.room.join_code, state)

    def tearDown(self):
        from rooms import consumers as room_consumers
        room_consumers.reset_redis_client()
        super().tearDown()

    async def test_client_receives_snapshot_on_connect(self):
        self._seed_active_round_state()
        # Pre-seed a snapshot in Redis (list of JSON strings)
        snapshot_payload = {"type": "drawing.stroke", "payload": {"data": "test"}}
        room_redis.append_canvas_stroke(
            self.fake_redis, 
            self.room.join_code, 
            json.dumps(snapshot_payload).encode()
        )

        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        
        messages = await _connect_and_drain_initial_sync(communicator, self.room.join_code, expects_game_active=True)

        # Client should have received the replayed event in the burst.
        self.assertTrue(any(m == snapshot_payload for m in messages))

        await communicator.disconnect()

    async def test_snapshot_accumulation(self):
        self._seed_active_round_state()
        drawer_socket = WebsocketCommunicator(_TEST_APP, _ws_url(self.room.join_code), headers=_session_headers(self.drawer_session_key))
        sync_viewer = WebsocketCommunicator(_TEST_APP, _ws_url(self.room.join_code), headers=_session_headers(self.viewer_session_key))
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code, expects_game_active=True)
        await _connect_and_drain_initial_sync(sync_viewer, self.room.join_code, expects_game_active=True)
        strokes = [{"type": "drawing.stroke", "payload": {"id": 1}}, {"type": "drawing.stroke", "payload": {"id": 2}}, {"type": "drawing.end_stroke", "payload": {}}]
        for s in strokes:
            await drawer_socket.send_json_to(s)
            await _receive_until_type(sync_viewer, s["type"])
        new_viewer_key = await _create_room_member(self.room.id, "Late Bob")
        viewer_socket = WebsocketCommunicator(_TEST_APP, _ws_url(self.room.join_code), headers=_session_headers(new_viewer_key))
        messages = await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code, expects_game_active=True)
        replayed = [m for m in messages if m.get("type", "").startswith("drawing.")]
        self.assertEqual(replayed, strokes)
        await drawer_socket.disconnect()
        await viewer_socket.disconnect()
        await sync_viewer.disconnect()

    async def test_snapshot_isolation_between_rounds(self):
        self._seed_active_round_state()
        drawer_socket = WebsocketCommunicator(_TEST_APP, _ws_url(self.room.join_code), headers=_session_headers(self.drawer_session_key))
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code, expects_game_active=True)
        sync_viewer = WebsocketCommunicator(_TEST_APP, _ws_url(self.room.join_code), headers=_session_headers(self.viewer_session_key))
        await _connect_and_drain_initial_sync(sync_viewer, self.room.join_code, expects_game_active=True)
        await drawer_socket.send_json_to({"type": "drawing.stroke", "payload": {"test": 1}})
        await _receive_until_type(sync_viewer, "drawing.stroke")
        self.assertEqual(len(room_redis.get_canvas_snapshot(self.fake_redis, self.room.join_code)), 1)
        # Completing a round progresses the game to the next round, so the test
        # must provide one unused snapshot word for the transition to stay valid.
        await database_sync_to_async(GameWord.objects.create)(game=self.game, text="planet")
        await database_sync_to_async(game_services.complete_round_due_to_timer)(self.round.id)
        new_viewer_key = await _create_room_member(self.room.id, "Intermission Bob")
        v_socket = WebsocketCommunicator(_TEST_APP, _ws_url(self.room.join_code), headers=_session_headers(new_viewer_key))
        await _connect_and_drain_initial_sync(v_socket, self.room.join_code, expects_game_active=True)
        drawn = [m for m in _drain_output_queue_nowait(v_socket) if m.get("type", "").startswith("drawing.")]
        self.assertEqual(drawn, [])
        await drawer_socket.disconnect()
        await sync_viewer.disconnect()
        await v_socket.disconnect()

    async def test_no_snapshot_sent_if_redis_empty(self):
        # Redis is empty
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        
        await _connect_and_drain_initial_sync(communicator, self.room.join_code, expects_game_active=False)

        # Should receive nothing after the initial handshake
        self.assertTrue(await communicator.receive_nothing())

        await communicator.disconnect()
