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

from config.routing import _TEST_APP
from rooms.models import Player, Room
from rooms.tests.test_consumers import _ws_url, _session_headers, _create_room_member
from rooms import consumers as room_consumers
from rooms import redis as room_redis
from games import redis as game_redis


class DrawingEventTests(TransactionTestCase):
    """Tests for drawer authorization and event broadcasting."""

    def setUp(self):
        self.fake_redis = fakeredis.FakeRedis()
        room_consumers._redis_client = self.fake_redis

        self.room = Room.objects.create(
            name="Drawing Room",
            join_code="DRAW1234",
            status=Room.Status.IN_PROGRESS,  # Active game needed for drawing
        )
        
        # Create two participants
        self.drawer_session_key = _create_room_member(self.room.id, "Drawer")
        self.drawer_player = Player.objects.get(room=self.room, session_key=self.drawer_session_key)
        
        self.viewer_session_key = _create_room_member(self.room.id, "Viewer")
        self.viewer_player = Player.objects.get(room=self.room, session_key=self.viewer_session_key)

        # Set the active drawer in Redis turn state
        game_redis.set_turn_state(self.fake_redis, self.room.join_code, {"drawer_id": self.drawer_player.id})

    def tearDown(self):
        room_consumers._redis_client = None

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
        
        await drawer_socket.connect()
        await viewer_socket.connect()

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
        
        await drawer_socket.connect()
        await viewer_socket.connect()

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
        await drawer_socket.connect()

        # First, ensure there is a snapshot
        room_redis.set_canvas_snapshot(self.fake_redis, self.room.join_code, b'{"some":"data"}')
        self.assertIsNotNone(room_redis.get_canvas_snapshot(self.fake_redis, self.room.join_code))

        # Drawer clears the canvas
        await drawer_socket.send_json_to({"type": "drawing.clear"})
        
        # Give it a tiny bit of time to process
        await drawer_socket.receive_nothing() # Wait for possible async task

        # Snapshot should be gone from Redis
        self.assertIsNone(room_redis.get_canvas_snapshot(self.fake_redis, self.room.join_code))

        await drawer_socket.disconnect()

    async def test_drawing_not_allowed_in_lobby(self):
        # Move room back to lobby
        self.room.status = Room.Status.LOBBY
        self.room.save()

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
        
        await drawer_socket.connect()
        await viewer_socket.connect()

        # "Drawer" tries to draw in lobby
        await drawer_socket.send_json_to({
            "type": "drawing.stroke",
            "payload": {"wont": "work"}
        })

        # Viewer should receive nothing
        self.assertTrue(await viewer_socket.receive_nothing())

        await drawer_socket.disconnect()
        await viewer_socket.disconnect()


class SnapshotSyncTests(TransactionTestCase):
    """Tests for canvas snapshot recovery on connection."""

    def setUp(self):
        self.fake_redis = fakeredis.FakeRedis()
        room_consumers._redis_client = self.fake_redis

        self.room = Room.objects.create(
            name="Snapshot Room",
            join_code="SNAP1234",
            status=Room.Status.IN_PROGRESS,
        )
        self.session_key = _create_room_member(self.room.id, "Alice")

    def tearDown(self):
        room_consumers._redis_client = None

    async def test_client_receives_snapshot_on_connect(self):
        # Pre-seed a snapshot in Redis
        snapshot_payload = {"all_strokes": "saved_data"}
        room_redis.set_canvas_snapshot(
            self.fake_redis, 
            self.room.join_code, 
            json.dumps(snapshot_payload).encode()
        )

        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        # Client should receive the snapshot as the first message (or after presence updates)
        # Note: Presence updates currently don't broadcast to self in connect/disconnect yet
        # but they might in later tickets.
        response = await communicator.receive_json_from()
        self.assertEqual(response["type"], "drawing.snapshot")
        self.assertEqual(response["payload"], snapshot_payload)

        await communicator.disconnect()

    async def test_no_snapshot_sent_if_redis_empty(self):
        # Redis is empty
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        # Should receive nothing because there's no snapshot
        self.assertTrue(await communicator.receive_nothing())

        await communicator.disconnect()
