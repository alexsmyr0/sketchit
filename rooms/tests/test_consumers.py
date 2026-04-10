"""
Tests for rooms.consumers.RoomConsumer

These are integration-level WebSocket tests that go through the full channel
routing and session middleware stack.  They use TransactionTestCase because
async tests that access the database via database_sync_to_async need
real commits visible across threads/tasks — Django's default TestCase wraps
everything in a rolled-back transaction which would hide those rows.

Test application
----------------
A minimal ASGI app is built from AuthMiddlewareStack + URLRouter so we can
exercise the session middleware without the AllowedHostsOriginValidator that
the production ASGI app includes (that validator checks the HTTP Origin header
which WebsocketCommunicator omits by default).
"""

import json

import fakeredis
from asgiref.sync import async_to_sync
from channels.auth import AuthMiddlewareStack
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.sessions.backends.db import SessionStore
from django.test import TransactionTestCase

from config.routing import websocket_urlpatterns
from rooms.consumers import _room_group_name
from rooms.models import Player, Room
from words.models import Word, WordPack, WordPackEntry

# ---------------------------------------------------------------------------
# Test-scoped ASGI application
# ---------------------------------------------------------------------------

_TEST_APP = AuthMiddlewareStack(URLRouter(websocket_urlpatterns))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ws_url(join_code: str) -> str:
    return f"/ws/rooms/{join_code}/"


def _session_headers(session_key: str) -> list[tuple[bytes, bytes]]:
    return [(b"cookie", f"sessionid={session_key}".encode())]


@database_sync_to_async
def _create_session_key() -> str:
    session = SessionStore()
    session.save()
    return session.session_key


@database_sync_to_async
def _create_room_member(room_id: int, display_name: str) -> str:
    session = SessionStore()
    session.save()
    room = Room.objects.get(pk=room_id)
    Player.objects.create(
        room=room,
        session_key=session.session_key,
        display_name=display_name,
        session_expires_at=session.get_expiry_date(),
    )
    return session.session_key


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class RoomConsumerConnectTests(TransactionTestCase):
    """Tests for the WebSocket connect / disconnect lifecycle."""

    def setUp(self):
        self.channel_layer = get_channel_layer()
        async_to_sync(self.channel_layer.flush)()

        from rooms import consumers as room_consumers
        self.fake_redis = fakeredis.FakeRedis()
        room_consumers._redis_client = self.fake_redis

        self.word_pack = WordPack.objects.create(name="Test Pack")
        test_word = Word.objects.create(text="rocket")
        WordPackEntry.objects.create(word_pack=self.word_pack, word=test_word)

        self.room = Room.objects.create(
            name="Test Room",
            join_code="TEST1234",
            visibility=Room.Visibility.PRIVATE,
            word_pack=self.word_pack,
        )

        # Use the HTTP test client to create a real Django session so we can
        # pass the session cookie to the WebSocket communicator.
        response = self.client.post(
            f"/rooms/{self.room.join_code}/join/",
            data=json.dumps({"display_name": "Alice"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.session_key = self.client.session.session_key
        self.player = Player.objects.get(room=self.room, session_key=self.session_key)

    def tearDown(self):
        async_to_sync(self.channel_layer.flush)()
        super().tearDown()
        from rooms import consumers as room_consumers
        room_consumers._redis_client = None

    def _group_members(self, group_name: str) -> dict[str, float]:
        return self.channel_layer.groups.get(group_name, {})

    async def test_connect_accepts_valid_room_member(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.disconnect()

    async def test_connect_adds_socket_to_room_group(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        connected, _ = await communicator.connect()

        self.assertTrue(connected)
        self.assertEqual(
            len(self._group_members(_room_group_name(self.room.join_code))),
            1,
        )

        await communicator.disconnect()

    async def test_connect_rejects_unknown_room(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url("ZZZZ9999"),
            headers=_session_headers(self.session_key),
        )
        connected, code = await communicator.connect()
        self.assertFalse(connected)
        self.assertEqual(code, 4004)

    async def test_connect_rejects_non_member_session(self):
        other_session_key = await _create_session_key()

        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(other_session_key),
        )
        connected, code = await communicator.connect()
        self.assertFalse(connected)
        self.assertEqual(code, 4003)

    async def test_connect_rejects_request_with_no_session_cookie(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            # No session cookie headers at all.
        )
        connected, code = await communicator.connect()
        self.assertFalse(connected)
        self.assertEqual(code, 4001)

    async def test_connect_normalises_join_code_to_uppercase(self):
        lower_code = self.room.join_code.lower()
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(lower_code),
            headers=_session_headers(self.session_key),
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.disconnect()

    async def test_multiple_room_members_share_the_same_group(self):
        second_session_key = await _create_room_member(self.room.id, "Bob")

        first = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        second = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(second_session_key),
        )

        first_connected, _ = await first.connect()
        second_connected, _ = await second.connect()

        self.assertTrue(first_connected)
        self.assertTrue(second_connected)
        self.assertEqual(
            len(self._group_members(_room_group_name(self.room.join_code))),
            2,
        )

        await first.disconnect()
        await second.disconnect()

    async def test_disconnect_removes_socket_from_room_group(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        self.assertEqual(
            len(self._group_members(_room_group_name(self.room.join_code))),
            1,
        )

        await communicator.disconnect()

        self.assertEqual(
            self._group_members(_room_group_name(self.room.join_code)),
            {},
        )

    async def test_presence_state_updates_on_connect_and_disconnect(self):
        # Force initial state to disconnected
        self.player.connection_status = Player.ConnectionStatus.DISCONNECTED
        await database_sync_to_async(self.player.save)()

        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        # Check DB update
        await database_sync_to_async(self.player.refresh_from_db)()
        self.assertEqual(self.player.connection_status, Player.ConnectionStatus.CONNECTED)

        # Check Redis update
        from rooms import redis as room_redis
        presence = room_redis.get_presence(self.fake_redis, self.room.join_code)
        self.assertIn(self.session_key, presence)

        await communicator.disconnect()

        # Check DB disconnect update
        await database_sync_to_async(self.player.refresh_from_db)()
        self.assertEqual(self.player.connection_status, Player.ConnectionStatus.DISCONNECTED)

        # Check Redis disconnect update
        presence = room_redis.get_presence(self.fake_redis, self.room.join_code)
        self.assertNotIn(self.session_key, presence)

    async def test_disconnect_does_not_reassign_host(self):
        second_session_key = await _create_room_member(self.room.id, "Bob")
        second_player = await database_sync_to_async(Player.objects.get)(
            room=self.room,
            session_key=second_session_key,
        )
        self.room.host = self.player
        await database_sync_to_async(self.room.save)(update_fields=["host"])

        host_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        member_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(second_session_key),
        )

        host_connected, _ = await host_socket.connect()
        member_connected, _ = await member_socket.connect()
        self.assertTrue(host_connected)
        self.assertTrue(member_connected)

        await host_socket.disconnect()

        await database_sync_to_async(self.room.refresh_from_db)()
        self.assertEqual(self.room.host_id, self.player.id)
        self.assertNotEqual(self.room.host_id, second_player.id)

        await member_socket.disconnect()

    async def test_reconnect_reuses_same_participant_row(self):
        first_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        connected, _ = await first_socket.connect()
        self.assertTrue(connected)

        original_player_id = self.player.id

        await first_socket.disconnect()
        await database_sync_to_async(self.player.refresh_from_db)()
        self.assertEqual(
            self.player.connection_status,
            Player.ConnectionStatus.DISCONNECTED,
        )

        second_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        reconnected, _ = await second_socket.connect()
        self.assertTrue(reconnected)

        refreshed_player = await database_sync_to_async(Player.objects.get)(
            room=self.room,
            session_key=self.session_key,
        )
        self.assertEqual(refreshed_player.id, original_player_id)
        self.assertEqual(
            await database_sync_to_async(
                Player.objects.filter(room=self.room, session_key=self.session_key).count
            )(),
            1,
        )

        await second_socket.disconnect()

    async def test_presence_stays_connected_until_last_same_session_socket_disconnects(self):
        first = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        second = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )

        first_connected, _ = await first.connect()
        second_connected, _ = await second.connect()
        self.assertTrue(first_connected)
        self.assertTrue(second_connected)

        await first.disconnect()

        await database_sync_to_async(self.player.refresh_from_db)()
        self.assertEqual(self.player.connection_status, Player.ConnectionStatus.CONNECTED)

        from rooms import redis as room_redis
        presence = room_redis.get_presence(self.fake_redis, self.room.join_code)
        self.assertIn(self.session_key, presence)

        await second.disconnect()

        await database_sync_to_async(self.player.refresh_from_db)()
        self.assertEqual(
            self.player.connection_status,
            Player.ConnectionStatus.DISCONNECTED,
        )
        presence = room_redis.get_presence(self.fake_redis, self.room.join_code)
        self.assertNotIn(self.session_key, presence)

    async def test_disconnect_does_not_raise_when_connect_was_rejected(self):
        # Rejected connections must not cause errors in disconnect.
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url("ZZZZ9999"),
            headers=_session_headers(self.session_key),
        )
        connected, _ = await communicator.connect()
        self.assertFalse(connected)
        # disconnect() on an already-rejected communicator should be safe.
        await communicator.disconnect()

    async def test_receive_json_echoes_message(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to({"type": "echo", "message": "hello world"})
        response = await communicator.receive_json_from()
        
        self.assertEqual(response["type"], "echo_reply")
        self.assertEqual(response["message"], "Echo: hello world")
        
        await communicator.disconnect()


class RoomGroupNameTests(TransactionTestCase):
    """Tests for the room group naming helper."""

    async def test_group_name_uses_room_prefix(self):
        self.assertEqual(_room_group_name("ABC12345"), "room_ABC12345")

    async def test_group_name_is_stable_for_same_join_code(self):
        self.assertEqual(_room_group_name("TEST1234"), _room_group_name("TEST1234"))

    async def test_different_rooms_have_different_group_names(self):
        self.assertNotEqual(_room_group_name("ROOM0001"), _room_group_name("ROOM0002"))
