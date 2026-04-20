"""
Tests for drawing event broadcasting and snapshot synchronization.

These tests verify that only the active drawer can broadcast drawing events
and that the server correctly manages the canvas snapshot in Redis for
reconnect recovery.
"""

import json
import fakeredis
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase

from asgiref.sync import async_to_sync
from rooms.models import Player, Room
from games.models import Game, GameWord, Round
from rooms.tests.test_consumers import (
    _ws_url, _session_headers, _create_room_member, _TEST_APP,
    _receive_until_type, _connect_and_receive_initial_room_state,
    _connect_and_drain_initial_sync
)
from rooms import consumers as room_consumers
from rooms import redis as room_redis
from games import redis as game_redis
from games import services as game_services
from words.models import Word, WordPack, WordPackEntry


class DrawingEventTests(TransactionTestCase):
    """Tests for drawer authorization and event broadcasting."""

    def setUp(self):
        self.fake_redis = fakeredis.FakeRedis()
        room_consumers._redis_client = self.fake_redis
        from games import services as game_services
        from games import runtime as game_runtime
        self._orig_services_redis = game_services._get_redis_client
        self._orig_runtime_redis = game_runtime._redis_client
        game_services._get_redis_client = lambda: self.fake_redis
        game_runtime._redis_client = self.fake_redis
        from games import services as game_services
        from games import runtime as game_runtime
        self._orig_services_redis = game_services._get_redis_client
        self._orig_runtime_redis = game_runtime._redis_client
        game_services._get_redis_client = lambda: self.fake_redis
        game_runtime._redis_client = self.fake_redis

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

        # Set the active drawer in Redis turn state using the new runtime field name
        game_redis.set_turn_state(self.fake_redis, self.room.join_code, {"drawer_participant_id": self.drawer_player.id})

    def tearDown(self):
        room_consumers._redis_client = None
        from games import services as game_services
        from games import runtime as game_runtime
        game_services._get_redis_client = self._orig_services_redis
        game_runtime._redis_client = self._orig_runtime_redis
        super().tearDown()

    async def test_drawer_can_broadcast_stroke(self):
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
        
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code)
        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code)

        # Drawer sends a stroke
        stroke_data = {"lines": [[0,0], [10,10]], "color": "red"}
        await drawer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": stroke_data
        })

        # Viewer should receive the broadcast
        response = await viewer_socket.receive_json_from()
        self.assertEqual(response["type"], "drawing.stroke")
        self.assertEqual(response["payload"], stroke_data)

        # Drawer should NOT receive their own broadcast
        self.assertTrue(await drawer_socket.receive_nothing())

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_drawing_end_stroke_broadcast(self):
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
        
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code)
        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code)

        # Drawer sends an end stroke
        await drawer_socket.send_json_to({
            "type": "drawing.end_stroke",
            "payload": {}
        })

        # Viewer should receive the broadcast
        response = await viewer_socket.receive_json_from()
        self.assertEqual(response["type"], "drawing.end_stroke")

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_multiple_viewers_receive_broadcast(self):
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
        
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code)
        await _connect_and_drain_initial_sync(viewer1_socket, self.room.join_code)
        await _connect_and_drain_initial_sync(viewer2_socket, self.room.join_code)

        stroke_data = {"lines": [[0,0], [10,10]]}
        await drawer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": stroke_data
        })

        # Both viewers should receive the broadcast
        resp1 = await viewer1_socket.receive_json_from()
        resp2 = await viewer2_socket.receive_json_from()
        
        self.assertEqual(resp1["payload"], stroke_data)
        self.assertEqual(resp2["payload"], stroke_data)

        await drawer_socket.disconnect()
        await viewer1_socket.disconnect()
        await viewer2_socket.disconnect()

    async def test_non_drawer_cannot_broadcast_stroke(self):
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
        
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code)
        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code)

        # Viewer (non-drawer) tries to send a stroke
        await viewer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": {"naughty": "secret"}
        })

        # Drawer should receive nothing
        self.assertTrue(await drawer_socket.receive_nothing())

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_drawing_clear_deletes_redis_snapshot(self):
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
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code)
        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code)

        # First, ensure there is a snapshot (it's now a list of JSON strings)
        stroke_data = b'{"type":"drawing.stroke", "payload":{}}'
        room_redis.append_canvas_stroke(self.fake_redis, self.room.join_code, stroke_data)
        self.assertEqual(len(room_redis.get_canvas_snapshot(self.fake_redis, self.room.join_code)), 1)

        # Viewer should receive the replayed stroke first
        replay = await viewer_socket.receive_json_from()
        self.assertEqual(replay["type"], "drawing.stroke")

        # Drawer clears the canvas
        await drawer_socket.send_json_to({"type": "drawing.clear"})
        
        # Viewer should receive the clear event broadcast
        response = await viewer_socket.receive_json_from()
        self.assertEqual(response["type"], "drawing.clear")

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
        
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code)
        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code)

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


class SnapshotSyncTests(TransactionTestCase):
    """Tests for canvas snapshot recovery on connection."""

    def setUp(self):
        self.fake_redis = fakeredis.FakeRedis()
        room_consumers._redis_client = self.fake_redis
        from games import services as game_services
        from games import runtime as game_runtime
        self._orig_services_redis = game_services._get_redis_client
        self._orig_runtime_redis = game_runtime._redis_client
        game_services._get_redis_client = lambda: self.fake_redis
        game_runtime._redis_client = self.fake_redis

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

        # Seed turn state so consumer sees an active round
        game_redis.set_turn_state(self.fake_redis, self.room.join_code, {
            "phase": "round",
            "round_id": "1",
            "drawer_participant_id": str(self.drawer_player.id)
        })
        self.game = Game.objects.create(room=self.room)

    def tearDown(self):
        room_consumers._redis_client = None
        from games import services as game_services
        from games import runtime as game_runtime
        game_services._get_redis_client = self._orig_services_redis
        game_runtime._redis_client = self._orig_runtime_redis
        super().tearDown()

    async def test_client_receives_snapshot_on_connect(self):
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
        
        await _connect_and_receive_initial_room_state(communicator, self.room.join_code)

        # Client should receive the replayed event
        response = await communicator.receive_json_from()
        self.assertEqual(response, snapshot_payload)

        await communicator.disconnect()

    async def test_snapshot_accumulation(self):
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        # Viewer to synchronize processing
        sync_viewer = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code)
        await _connect_and_drain_initial_sync(sync_viewer, self.room.join_code)

        # Send 2 strokes and an end_stroke
        strokes = [
            {"type": "drawing.stroke", "payload": {"id": 1}},
            {"type": "drawing.stroke", "payload": {"id": 2}},
            {"type": "drawing.end_stroke", "payload": {}},
        ]
        for s in strokes:
            await drawer_socket.send_json_to(s)
            # Wait for broadcast to ensure Redis is updated
            await sync_viewer.receive_json_from()
        await sync_viewer.disconnect()

        # Connect a new viewer
        new_viewer_key = await _create_room_member(self.room.id, "Late Bob")
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(new_viewer_key),
        )
        
        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code)

        # Should receive all 3 messages in order
        for expected in strokes:
            response = await viewer_socket.receive_json_from()
            self.assertEqual(response, expected)

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_snapshot_isolation_between_rounds(self):
        # 1. Start round, send drawing
        drawer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.drawer_session_key),
        )
        await _connect_and_drain_initial_sync(drawer_socket, self.room.join_code)
        # Viewer to synchronize processing
        sync_viewer = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        await _connect_and_drain_initial_sync(sync_viewer, self.room.join_code)

        await drawer_socket.send_json_to({"type": "drawing.stroke", "payload": {"test": 1}})
        # Wait for broadcast
        await sync_viewer.receive_json_from()
        await sync_viewer.disconnect()
        
        # Verify snapshot exists
        self.assertEqual(len(room_redis.get_canvas_snapshot(self.fake_redis, self.room.join_code)), 1)
        
        # 2. Simulate round end using the actual service path
        # We need a Round object for the service call
        from channels.db import database_sync_to_async
        game_word = await database_sync_to_async(GameWord.objects.create)(
            game=self.game, text="rocket"
        )
        round_obj = await database_sync_to_async(Round.objects.create)(
            game=self.game,
            sequence_number=1,
            drawer_participant=self.drawer_player,
            drawer_nickname=self.drawer_player.display_name,
            selected_game_word=game_word,
        )
        # Wait, let's just use the service logic properly
        await database_sync_to_async(game_services.complete_round_due_to_timer)(round_obj.id)
        
        # 3. New viewer connects during intermission (no active round)
        # We manually seed an intermission turn state to ensure _send_initial_canvas_snapshot
        # doesn't just return early due to missing turn_state (which would mask a leak).
        game_redis.set_turn_state(self.fake_redis, self.room.join_code, {
            "phase": "intermission",
            "round_id": str(round_obj.id)
        })

        new_viewer_key = await _create_room_member(self.room.id, "Late Bob")
        viewer_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(new_viewer_key),
        )
        await _connect_and_drain_initial_sync(viewer_socket, self.room.join_code)
        
        # Viewer should receive NOTHING (no snapshot from old round) because the service cleared it
        self.assertTrue(await viewer_socket.receive_nothing())

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()

    async def test_no_snapshot_sent_if_redis_empty(self):
        # Redis is empty
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.viewer_session_key),
        )
        
        await _connect_and_receive_initial_room_state(communicator, self.room.join_code)

        # Now it should receive nothing because there's no snapshot
        self.assertTrue(await communicator.receive_nothing())

        await communicator.disconnect()
