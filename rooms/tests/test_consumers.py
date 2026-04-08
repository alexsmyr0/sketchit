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
from datetime import timedelta

from channels.auth import AuthMiddlewareStack
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase
from django.urls import re_path
from django.utils import timezone

from rooms.consumers import RoomConsumer, _room_group_name
from rooms.models import Player, Room

# ---------------------------------------------------------------------------
# Test-scoped ASGI application
# ---------------------------------------------------------------------------

_TEST_APP = AuthMiddlewareStack(
    URLRouter(
        [
            re_path(
                r"^ws/rooms/(?P<join_code>[A-Za-z0-9]{8})/$",
                RoomConsumer.as_asgi(),
            ),
        ]
    )
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ws_url(join_code: str) -> str:
    return f"/ws/rooms/{join_code}/"


def _session_headers(session_key: str) -> list[tuple[bytes, bytes]]:
    return [(b"cookie", f"sessionid={session_key}".encode())]


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class RoomConsumerConnectTests(TransactionTestCase):
    """Tests for the WebSocket connect / disconnect lifecycle."""

    def setUp(self):
        session_expires_at = timezone.now() + timedelta(hours=1)

        self.room = Room.objects.create(
            name="Test Room",
            join_code="TEST1234",
            visibility=Room.Visibility.PRIVATE,
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

    async def test_connect_accepts_valid_room_member(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
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
        # Create a second client (different session) that has NOT joined the room.
        other_client = self.client_class()
        # Force a session for the other client by hitting any view.
        other_client.get("/rooms/ZZZZ0000/")  # 404 is fine — just needs a session
        other_session_key = other_client.session.session_key

        # other_session_key may be None if no session was created yet.  Use an
        # invented key directly if needed.
        if not other_session_key:
            from django.contrib.sessions.backends.db import SessionStore
            s = SessionStore()
            s.save()
            other_session_key = s.session_key

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


class RoomGroupNameTests(TransactionTestCase):
    """Tests for the room group naming helper."""

    async def test_group_name_uses_room_prefix(self):
        self.assertEqual(_room_group_name("ABC12345"), "room_ABC12345")

    async def test_group_name_is_stable_for_same_join_code(self):
        self.assertEqual(_room_group_name("TEST1234"), _room_group_name("TEST1234"))

    async def test_different_rooms_have_different_group_names(self):
        self.assertNotEqual(_room_group_name("ROOM0001"), _room_group_name("ROOM0002"))
