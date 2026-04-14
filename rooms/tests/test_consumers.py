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

import asyncio
from datetime import timedelta
import json

import fakeredis
from asgiref.sync import async_to_sync
from channels.auth import AuthMiddlewareStack
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from config.routing import websocket_urlpatterns
from games import runtime as game_runtime
from games.models import Round
from games.services import evaluate_guess_for_round, start_game_for_room
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


@database_sync_to_async
def _create_connected_room_member(room_id: int, display_name: str) -> str:
    session = SessionStore()
    session.save()
    room = Room.objects.get(pk=room_id)
    Player.objects.create(
        room=room,
        session_key=session.session_key,
        display_name=display_name,
        connection_status=Player.ConnectionStatus.CONNECTED,
        session_expires_at=timezone.now() + timedelta(hours=1),
    )
    return session.session_key


@database_sync_to_async
def _start_game(room_id: int):
    room = Room.objects.get(pk=room_id)
    started = start_game_for_room(room)
    return started.first_round.id


@database_sync_to_async
def _end_round_by_correct_guess(round_id: int) -> None:
    round = Round.objects.select_related("selected_game_word").get(pk=round_id)
    guesser = (
        Player.objects.filter(
            room_id=round.game.room_id,
            participation_status=Player.ParticipationStatus.PLAYING,
            connection_status=Player.ConnectionStatus.CONNECTED,
            created_at__lte=round.started_at,
        )
        .exclude(pk=round.drawer_participant_id)
        .order_by("created_at", "id")
        .first()
    )
    assert guesser is not None
    evaluate_guess_for_round(round, guesser, round.selected_game_word.text)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class RoomConsumerConnectTests(TransactionTestCase):
    """Tests for the WebSocket connect / disconnect lifecycle."""

    def setUp(self):
        self.channel_layer = get_channel_layer()
        async_to_sync(self.channel_layer.flush)()

        from rooms import consumers as room_consumers
        game_runtime.reset_runtime_state_for_tests()
        self.fake_redis = fakeredis.FakeRedis()
        room_consumers._redis_client = self.fake_redis
        game_runtime._redis_client = self.fake_redis

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
        game_runtime.reset_runtime_state_for_tests()
        super().tearDown()
        from rooms import consumers as room_consumers
        room_consumers._redis_client = None

    def _group_members(self, group_name: str) -> dict[str, float]:
        return self.channel_layer.groups.get(group_name, {})

    async def _receive_until_type(self, communicator, event_type: str, attempts: int = 20):
        for _ in range(attempts):
            event = await communicator.receive_json_from(timeout=1)
            if event.get("type") == event_type:
                return event
        self.fail(f"Did not receive expected event type '{event_type}'.")

    async def _connect_and_receive_initial_room_state(
        self,
        communicator,
        *,
        drain_duplicate_room_states: bool = False,
    ):
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        room_state = await self._receive_until_type(communicator, "room.state")
        self.assertEqual(room_state["payload"]["room"]["join_code"], self.room.join_code)
        if drain_duplicate_room_states:
            while True:
                try:
                    next_event = await communicator.receive_json_from(timeout=0.05)
                except asyncio.TimeoutError:
                    break
                self.assertEqual(next_event.get("type"), "room.state")
                room_state = next_event
        return room_state

    async def test_connect_accepts_valid_room_member(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await self._connect_and_receive_initial_room_state(
            communicator,
            drain_duplicate_room_states=True,
        )
        await communicator.disconnect()

    async def test_connect_adds_socket_to_room_group(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await self._connect_and_receive_initial_room_state(
            communicator,
            drain_duplicate_room_states=True,
        )
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
        await self._connect_and_receive_initial_room_state(
            communicator,
            drain_duplicate_room_states=True,
        )
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

        await self._connect_and_receive_initial_room_state(
            first,
            drain_duplicate_room_states=True,
        )
        await self._connect_and_receive_initial_room_state(
            second,
            drain_duplicate_room_states=True,
        )
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
        await self._connect_and_receive_initial_room_state(
            communicator,
            drain_duplicate_room_states=True,
        )
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
        room_state = await self._connect_and_receive_initial_room_state(
            communicator,
            drain_duplicate_room_states=True,
        )
        self.assertEqual(
            room_state["payload"]["participants"][0]["connection_status"],
            Player.ConnectionStatus.CONNECTED,
        )

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

        await self._connect_and_receive_initial_room_state(
            host_socket,
            drain_duplicate_room_states=True,
        )
        await self._connect_and_receive_initial_room_state(
            member_socket,
            drain_duplicate_room_states=True,
        )

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
        await self._connect_and_receive_initial_room_state(
            first_socket,
            drain_duplicate_room_states=True,
        )

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
        await self._connect_and_receive_initial_room_state(
            second_socket,
            drain_duplicate_room_states=True,
        )

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

        await self._connect_and_receive_initial_room_state(
            first,
            drain_duplicate_room_states=True,
        )
        await self._connect_and_receive_initial_room_state(
            second,
            drain_duplicate_room_states=True,
        )

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
        await self._connect_and_receive_initial_room_state(
            communicator,
            drain_duplicate_room_states=True,
        )

        await communicator.send_json_to({"type": "echo", "message": "hello world"})
        response = await self._receive_until_type(communicator, "echo_reply")
        
        self.assertEqual(response["type"], "echo_reply")
        self.assertEqual(response["message"], "Echo: hello world")
        
        await communicator.disconnect()

    async def test_room_group_server_event_is_forwarded_to_connected_clients(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await self._connect_and_receive_initial_room_state(
            communicator,
            drain_duplicate_room_states=True,
        )

        event_payload = {
            "type": "round.timer",
            "payload": {
                "round_id": 7,
                "remaining_seconds": 42,
            },
        }
        await self.channel_layer.group_send(
            _room_group_name(self.room.join_code),
            {
                "type": "room.server_event",
                "event": event_payload,
            },
        )

        forwarded = await self._receive_until_type(communicator, "round.timer")
        self.assertEqual(forwarded, event_payload)

        await communicator.disconnect()

    @override_settings(
        SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True,
        SKETCHIT_ROUND_DURATION_SECONDS=3,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=3,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.1,
    )
    async def test_runtime_generated_round_events_reach_socket_end_to_end(self):
        await _create_connected_room_member(self.room.id, "Bob")
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await self._connect_and_receive_initial_room_state(communicator)

        await _start_game(self.room.id)

        round_started = None
        round_timer = None
        for _ in range(25):
            event = await communicator.receive_json_from(timeout=1)
            if event.get("type") == "round.started" and round_started is None:
                round_started = event
            if event.get("type") == "round.timer" and round_timer is None:
                round_timer = event
            if round_started is not None and round_timer is not None:
                break

        self.assertIsNotNone(round_started)
        self.assertIsNotNone(round_timer)
        self.assertIn("server_timestamp", round_started["payload"])
        self.assertIn("tick_sequence", round_timer["payload"])
        self.assertGreaterEqual(round_timer["payload"]["tick_sequence"], 1)

        await communicator.disconnect()

    @override_settings(
        SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True,
        SKETCHIT_ROUND_DURATION_SECONDS=5,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=2,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.1,
    )
    async def test_connect_mid_intermission_receives_round_state_sync_snapshot(self):
        await _create_connected_room_member(self.room.id, "Bob")
        initial_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await self._connect_and_receive_initial_room_state(initial_socket)

        first_round_id = await _start_game(self.room.id)
        await self._receive_until_type(initial_socket, "round.started")
        await _end_round_by_correct_guess(first_round_id)
        await self._receive_until_type(initial_socket, "round.intermission_started")
        await initial_socket.disconnect()

        reconnect_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await self._connect_and_receive_initial_room_state(reconnect_socket)

        round_state = await self._receive_until_type(reconnect_socket, "round.state")
        self.assertEqual(round_state["payload"]["phase"], "intermission")
        self.assertIn("tick_sequence", round_state["payload"])
        self.assertIn("server_timestamp", round_state["payload"])

        await reconnect_socket.disconnect()

    async def test_connect_sends_initial_room_state_snapshot(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )

        room_state = await self._connect_and_receive_initial_room_state(
            communicator,
            drain_duplicate_room_states=True,
        )

        self.assertEqual(room_state["type"], "room.state")
        self.assertEqual(
            room_state["payload"]["room"],
            {
                "name": self.room.name,
                "join_code": self.room.join_code,
                "visibility": self.room.visibility,
                "status": self.room.status,
            },
        )
        self.assertEqual(
            room_state["payload"]["host"],
            {
                "id": self.player.id,
                "display_name": self.player.display_name,
            },
        )
        self.assertEqual(
            room_state["payload"]["participants"],
            [
                {
                    "id": self.player.id,
                    "display_name": self.player.display_name,
                    "connection_status": Player.ConnectionStatus.CONNECTED,
                    "participation_status": self.player.participation_status,
                }
            ],
        )

        await communicator.disconnect()

    async def test_http_join_broadcasts_room_state_to_connected_peers(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await self._connect_and_receive_initial_room_state(
            communicator,
            drain_duplicate_room_states=True,
        )

        second_client = Client()
        response = second_client.post(
            f"/rooms/{self.room.join_code}/join/",
            data=json.dumps({"display_name": "Bob"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)

        room_state = await self._receive_until_type(communicator, "room.state")
        participant_names = [
            participant["display_name"]
            for participant in room_state["payload"]["participants"]
        ]
        self.assertEqual(participant_names, ["Alice", "Bob"])
        self.assertEqual(
            room_state["payload"]["participants"][1]["connection_status"],
            Player.ConnectionStatus.DISCONNECTED,
        )

        await communicator.disconnect()

    async def test_disconnect_broadcasts_room_state_with_disconnected_status(self):
        second_session_key = await _create_room_member(self.room.id, "Bob")
        first_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        second_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(second_session_key),
        )

        await self._connect_and_receive_initial_room_state(
            first_socket,
            drain_duplicate_room_states=True,
        )
        await self._connect_and_receive_initial_room_state(
            second_socket,
            drain_duplicate_room_states=True,
        )
        await self._receive_until_type(first_socket, "room.state")

        await second_socket.disconnect()

        room_state = await self._receive_until_type(first_socket, "room.state")
        bob_participant = next(
            participant
            for participant in room_state["payload"]["participants"]
            if participant["display_name"] == "Bob"
        )
        self.assertEqual(
            bob_participant["connection_status"],
            Player.ConnectionStatus.DISCONNECTED,
        )

        await first_socket.disconnect()

    async def test_reconnect_broadcasts_room_state_with_connected_status(self):
        second_session_key = await _create_room_member(self.room.id, "Bob")
        first_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        second_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(second_session_key),
        )

        await self._connect_and_receive_initial_room_state(
            first_socket,
            drain_duplicate_room_states=True,
        )
        await self._connect_and_receive_initial_room_state(
            second_socket,
            drain_duplicate_room_states=True,
        )
        await self._receive_until_type(first_socket, "room.state")

        await second_socket.disconnect()
        await self._receive_until_type(first_socket, "room.state")

        reconnect_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(second_session_key),
        )
        await self._connect_and_receive_initial_room_state(
            reconnect_socket,
            drain_duplicate_room_states=True,
        )

        room_state = await self._receive_until_type(first_socket, "room.state")
        bob_participant = next(
            participant
            for participant in room_state["payload"]["participants"]
            if participant["display_name"] == "Bob"
        )
        self.assertEqual(
            bob_participant["connection_status"],
            Player.ConnectionStatus.CONNECTED,
        )

        await reconnect_socket.disconnect()
        await first_socket.disconnect()

    async def test_second_socket_for_same_session_does_not_broadcast_room_state_to_peers(self):
        second_session_key = await _create_room_member(self.room.id, "Bob")
        alice_primary_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        bob_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(second_session_key),
        )

        await self._connect_and_receive_initial_room_state(
            alice_primary_socket,
            drain_duplicate_room_states=True,
        )
        await self._connect_and_receive_initial_room_state(
            bob_socket,
            drain_duplicate_room_states=True,
        )
        await self._receive_until_type(alice_primary_socket, "room.state")

        alice_second_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await self._connect_and_receive_initial_room_state(
            alice_second_socket,
            drain_duplicate_room_states=True,
        )

        self.assertTrue(await bob_socket.receive_nothing(timeout=0.2))

        await alice_second_socket.disconnect()
        await bob_socket.disconnect()
        await alice_primary_socket.disconnect()


class RoomGroupNameTests(TransactionTestCase):
    """Tests for the room group naming helper."""

    async def test_group_name_uses_room_prefix(self):
        self.assertEqual(_room_group_name("ABC12345"), "room_ABC12345")

    async def test_group_name_is_stable_for_same_join_code(self):
        self.assertEqual(_room_group_name("TEST1234"), _room_group_name("TEST1234"))

    async def test_different_rooms_have_different_group_names(self):
        self.assertNotEqual(_room_group_name("ROOM0001"), _room_group_name("ROOM0002"))
