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
import asyncio

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
from rooms.services import leave_participant
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


@database_sync_to_async
def _get_round_status(round_id: int) -> str | None:
    return Round.objects.get(pk=round_id).status


@database_sync_to_async
def _leave_room_member(*, redis_client, player_id: int) -> None:
    """Trigger the permanent leave lifecycle from the room service."""

    leave_participant(
        redis_client=redis_client,
        player_id=player_id,
    )


@database_sync_to_async
def _join_room_via_http(*, join_code: str, display_name: str) -> tuple[int, bytes]:
    """Join a room through the sync Django test client from an async test."""

    client = Client()
    response = client.post(
        f"/rooms/{join_code}/join/",
        data=json.dumps({"display_name": display_name}),
        content_type="application/json",
    )
    return response.status_code, response.content


async def _receive_until_type(communicator, event_type: str, attempts: int = 50):
    """Wait for and return a specific JSON event type, ignoring others (safe version)."""
    import asyncio, json
    for _ in range(attempts):
        try:
            raw = await asyncio.wait_for(communicator.output_queue.get(), timeout=2.0)
            if raw.get("type") == "websocket.send":
                event = json.loads(raw["text"])
                if event.get("type") == event_type:
                    return event
        except asyncio.TimeoutError:
            continue
    raise AssertionError(f"Did not receive expected event type '{event_type}'.")


async def _receive_output_json(communicator, *, timeout: float) -> dict:
    """Read one JSON websocket.send frame directly from the output queue."""
    raw = await asyncio.wait_for(communicator.output_queue.get(), timeout=timeout)
    if raw.get("type") != "websocket.send":
        raise AssertionError(f"Expected websocket.send frame, got {raw.get('type')!r}.")

    payload = raw.get("text")
    if not isinstance(payload, str):
        raise AssertionError(f"Expected text websocket payload, got {type(payload)!r}.")

    return json.loads(payload)


async def _drain_output_queue_safe(communicator, timeout: float = 0.2) -> list[dict]:
    """Timed drain of the communicator output queue without calling receive_output."""
    messages = []
    while True:
        try:
            messages.append(await _receive_output_json(communicator, timeout=timeout))
            timeout = 0.2
        except asyncio.TimeoutError:
            break
    return messages


async def _connect_and_receive_initial_room_state(
    communicator,
    join_code: str,
):
    """Connect and consume exactly the mandatory initial room.state event."""
    connected, _ = await communicator.connect()
    if not connected:
        raise AssertionError("WebSocket failed to connect.")

    room_state = await _receive_output_json(communicator, timeout=2.0)
    if room_state.get("type") != "room.state":
        raise AssertionError(
            f"Expected initial event 'room.state', got {room_state.get('type')!r}."
        )

    actual_join_code = room_state["payload"]["room"]["join_code"]
    if actual_join_code != join_code.upper():
        raise AssertionError(
            f"Expected room.state for {join_code.upper()}, got {actual_join_code}."
        )

    return room_state


async def _connect_and_drain_initial_sync(
    communicator: WebsocketCommunicator,
    join_code: str,
    expects_game_active: bool = False,
    timeout: float = 5.0,
) -> list[dict]:
    """Connect to a room and collect only the allowed initial handshake events."""
    connected, _ = await communicator.connect(timeout=timeout)
    if not connected:
        raise ConnectionError(f"Failed to connect to room {join_code}")

    first_msg = await _receive_output_json(communicator, timeout=4.0)
    if first_msg.get("type") != "room.state":
        raise AssertionError(
            f"Expected initial event 'room.state', got {first_msg.get('type')!r}."
        )

    actual_join_code = first_msg["payload"]["room"]["join_code"]
    if actual_join_code != join_code.upper():
        raise AssertionError(
            f"Expected room.state for {join_code.upper()}, got {actual_join_code}."
        )

    messages = [first_msg]
    allowed_followup_types = {
        "drawing.stroke",
        "drawing.end_stroke",
        "drawing.clear",
        "round.state",
        "round.timer",
        "round.intermission_timer",
        "round.started",
        "round.drawer_word",
        "scoreboard.state",
    }

    def _append_and_validate(message: dict) -> None:
        message_type = message.get("type")
        if message_type not in allowed_followup_types:
            raise AssertionError(
                f"Unexpected connect-time event {message_type!r} after room.state."
            )
        messages.append(message)

    if expects_game_active:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(4.0, timeout)

        while loop.time() < deadline:
            has_round_state = any(
                message.get("type") == "round.state" for message in messages
            )
            has_timer = any(
                message.get("type") in {"round.timer", "round.intermission_timer"}
                for message in messages
            )
            if has_round_state and has_timer:
                break

            try:
                remaining = deadline - loop.time()
                message = await _receive_output_json(
                    communicator,
                    timeout=min(1.0, remaining),
                )
            except asyncio.TimeoutError:
                continue

            _append_and_validate(message)

        if not any(message.get("type") == "round.state" for message in messages):
            raise AssertionError("Expected connect-time round.state sync event.")
        if not any(
            message.get("type") in {"round.timer", "round.intermission_timer"}
            for message in messages
        ):
            raise AssertionError(
                "Expected connect-time round timer or intermission timer sync event."
            )

        for message in await _drain_output_queue_safe(communicator, timeout=0.2):
            _append_and_validate(message)
    else:
        for message in await _drain_output_queue_safe(communicator, timeout=0.2):
            _append_and_validate(message)

    return messages



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
        room_consumers.reset_redis_client()
        self.fake_redis = fakeredis.FakeRedis()
        room_consumers._redis_client = self.fake_redis
        game_runtime._redis_client = self.fake_redis
        from games import services as game_services
        self._orig_services_redis = game_services._get_redis_client
        game_services._get_redis_client = lambda: self.fake_redis

        self.word_pack = WordPack.objects.create(name="Test Pack")
        test_word = Word.objects.create(text="rocket")
        WordPackEntry.objects.create(word_pack=self.word_pack, word=test_word)

        self.room = Room.objects.create(
            name="Test Room",
            join_code="TEST1234",
            visibility=Room.Visibility.PRIVATE,
            word_pack=self.word_pack,
        )
        session = SessionStore()
        session.save()
        self.session_key = session.session_key
        self.player = Player.objects.create(
            room=self.room,
            session_key=self.session_key,
            display_name="Alice",
            session_expires_at=session.get_expiry_date(),
        )
        self.room.host = self.player
        self.room.save(update_fields=["host", "updated_at"])

    def tearDown(self):
        async_to_sync(self.channel_layer.flush)()
        game_runtime.reset_runtime_state_for_tests()
        from rooms import consumers as room_consumers
        room_consumers._redis_client = None
        from games import services as game_services
        game_services._get_redis_client = self._orig_services_redis
        super().tearDown()

    def _group_members(self, group_name: str) -> dict[str, float]:
        return self.channel_layer.groups.get(group_name, {})

    async def _receive_until_type(self, communicator, event_type: str, attempts: int = 20):
        for _ in range(attempts):
            try:
                event = await communicator.receive_json_from(timeout=1)
            except TimeoutError:
                continue
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
        return room_state

    async def test_connect_accepts_valid_room_member(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await _connect_and_receive_initial_room_state(
            communicator,
            self.room.join_code,
        )
        await communicator.disconnect()

    async def test_connect_adds_socket_to_room_group(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await _connect_and_receive_initial_room_state(
            communicator,
            self.room.join_code,
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
        await _connect_and_receive_initial_room_state(
            communicator,
            self.room.join_code,
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

        await _connect_and_receive_initial_room_state(
            first,
            self.room.join_code,
        )
        await _connect_and_receive_initial_room_state(
            second,
            self.room.join_code,
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
        await _connect_and_receive_initial_room_state(
            communicator,
            self.room.join_code,
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
        room_state = await _connect_and_receive_initial_room_state(
            communicator,
            self.room.join_code,
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

        # Lobby disconnect is a permanent leave — the membership row is deleted,
        # not just marked DISCONNECTED.
        player_still_exists = await database_sync_to_async(
            Player.objects.filter(pk=self.player.id).exists
        )()
        self.assertFalse(player_still_exists)

        # Check Redis disconnect update
        presence = room_redis.get_presence(self.fake_redis, self.room.join_code)
        self.assertNotIn(self.session_key, presence)

    async def test_lobby_disconnect_reassigns_host(self):
        # Lobby disconnects are permanent leaves, so closing the host's socket
        # must hand ownership to the next connected participant.
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

        await _connect_and_receive_initial_room_state(
            host_socket,
            self.room.join_code,
        )
        await _connect_and_receive_initial_room_state(
            member_socket,
            self.room.join_code,
        )

        await host_socket.disconnect()

        await database_sync_to_async(self.room.refresh_from_db)()
        # The departing host's row is deleted; ownership transfers to Bob.
        self.assertEqual(self.room.host_id, second_player.id)

        await member_socket.disconnect()

    async def test_lobby_disconnect_deletes_participant_row(self):
        # In the lobby there is nothing to reconnect to, so the membership row
        # must be removed when the last socket closes (not just marked offline).
        socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await _connect_and_receive_initial_room_state(socket, self.room.join_code)

        await socket.disconnect()

        row_exists = await database_sync_to_async(
            Player.objects.filter(pk=self.player.id).exists
        )()
        self.assertFalse(row_exists)

    async def test_lobby_disconnect_socket_reconnect_rejected(self):
        # After a lobby disconnect the participant row is gone.  Opening a new
        # socket with the same session must be rejected (4003) because the
        # session is no longer a member of the room.  Re-entry requires going
        # through the HTTP join flow again.
        socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await _connect_and_receive_initial_room_state(socket, self.room.join_code)
        await socket.disconnect()

        reconnect = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        connected, close_code = await reconnect.connect()
        self.assertFalse(connected)
        self.assertEqual(close_code, 4003)

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

        await _connect_and_receive_initial_room_state(
            first,
            self.room.join_code,
        )
        await _connect_and_receive_initial_room_state(
            second,
            self.room.join_code,
        )

        await first.disconnect()

        await database_sync_to_async(self.player.refresh_from_db)()
        self.assertEqual(self.player.connection_status, Player.ConnectionStatus.CONNECTED)

        from rooms import redis as room_redis
        presence = room_redis.get_presence(self.fake_redis, self.room.join_code)
        self.assertIn(self.session_key, presence)

        await second.disconnect()

        # Both sockets gone → lobby permanent leave → row deleted.
        row_exists = await database_sync_to_async(
            Player.objects.filter(pk=self.player.id).exists
        )()
        self.assertFalse(row_exists)
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
        await _connect_and_receive_initial_room_state(
            communicator,
            self.room.join_code,
        )

        await communicator.send_json_to({"type": "echo", "message": "hello world"})
        response = await _receive_until_type(communicator, "echo_reply")
        
        self.assertEqual(response["type"], "echo_reply")
        self.assertEqual(response["message"], "Echo: hello world")
        
        await communicator.disconnect()

    async def test_room_group_server_event_is_forwarded_to_connected_clients(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await _connect_and_receive_initial_room_state(
            communicator,
            self.room.join_code,
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

        forwarded = await _receive_until_type(communicator, "round.timer")
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
        await _connect_and_receive_initial_room_state(communicator, self.room.join_code)

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
        await _connect_and_receive_initial_room_state(initial_socket, self.room.join_code)

        first_round_id = await _start_game(self.room.id)
        await _receive_until_type(initial_socket, "round.started")
        await _end_round_by_correct_guess(first_round_id)
        await _receive_until_type(initial_socket, "round.intermission_started")
        await initial_socket.disconnect()

        reconnect_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await _connect_and_receive_initial_room_state(reconnect_socket, self.room.join_code)

        round_state = await _receive_until_type(reconnect_socket, "round.state")
        self.assertEqual(round_state["payload"]["phase"], "intermission")
        self.assertIn("tick_sequence", round_state["payload"])
        self.assertIn("server_timestamp", round_state["payload"])

        await reconnect_socket.disconnect()

    @override_settings(
        SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True,
        SKETCHIT_ROUND_DURATION_SECONDS=5,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=0.4,
        SKETCHIT_LEADERBOARD_DURATION_SECONDS=2,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.05,
    )
    async def test_reconnect_during_leaderboard_receives_scoreboard_sync_and_no_active_round(self):
        @database_sync_to_async
        def _add_second_snapshot_word() -> None:
            second_word = Word.objects.create(text="planet")
            WordPackEntry.objects.create(word_pack=self.word_pack, word=second_word)

        await _add_second_snapshot_word()
        await _create_connected_room_member(self.room.id, "Bob")
        initial_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await _connect_and_receive_initial_room_state(initial_socket, self.room.join_code)

        first_round_id = await _start_game(self.room.id)
        await _receive_until_type(initial_socket, "round.started")
        await _end_round_by_correct_guess(first_round_id)
        await _receive_until_type(initial_socket, "round.intermission_started")

        second_round_started = await _receive_until_type(initial_socket, "round.started")
        second_round_id = second_round_started["payload"]["round_id"]
        await _end_round_by_correct_guess(second_round_id)
        await _receive_until_type(initial_socket, "game.finished")
        await _receive_until_type(initial_socket, "scoreboard.state")
        await initial_socket.disconnect()

        reconnect_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await _connect_and_receive_initial_room_state(reconnect_socket, self.room.join_code)

        scoreboard_state = await _receive_until_type(reconnect_socket, "scoreboard.state")
        self.assertEqual(scoreboard_state["payload"]["phase"], "leaderboard")
        self.assertIn("remaining_seconds", scoreboard_state["payload"])
        self.assertIn("entries", scoreboard_state["payload"])

        await reconnect_socket.send_json_to({
            "type": "guess.submit",
            "payload": {"text": "rocket"},
        })
        guess_error = await _receive_until_type(reconnect_socket, "guess.error")
        self.assertEqual(
            guess_error["payload"]["message"],
            "No active round in progress.",
        )

        await reconnect_socket.disconnect()

    async def test_connect_sends_initial_room_state_snapshot(self):
        communicator = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )

        room_state = await _connect_and_receive_initial_room_state(
            communicator,
            self.room.join_code,
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
                    "current_score": self.player.current_score,
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
        await _connect_and_receive_initial_room_state(
            communicator,
            self.room.join_code,
        )

        status_code, response_content = await _join_room_via_http(
            join_code=self.room.join_code,
            display_name="Bob",
        )
        self.assertEqual(status_code, 201, response_content)

        room_state = await _receive_until_type(communicator, "room.state")
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

        await _connect_and_receive_initial_room_state(
            first_socket,
            self.room.join_code,
        )
        await _connect_and_receive_initial_room_state(
            second_socket,
            self.room.join_code,
        )
        await _receive_until_type(first_socket, "room.state")

        await second_socket.disconnect()

        room_state = await _receive_until_type(first_socket, "room.state")
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

        await _connect_and_receive_initial_room_state(
            first_socket,
            self.room.join_code,
        )
        await _connect_and_receive_initial_room_state(
            second_socket,
            self.room.join_code,
        )
        await _receive_until_type(first_socket, "room.state")

        await second_socket.disconnect()
        await _receive_until_type(first_socket, "room.state")

        reconnect_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(second_session_key),
        )
        await _connect_and_receive_initial_room_state(
            reconnect_socket,
            self.room.join_code,
        )

        room_state = await _receive_until_type(first_socket, "room.state")
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

    @override_settings(
        SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True,
        SKETCHIT_ROUND_DURATION_SECONDS=5,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=2,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.05,
        SKETCHIT_DRAWER_DISCONNECT_GRACE_SECONDS=0.6,
    )
    async def test_drawer_reconnect_before_grace_deadline_resumes_round(self):
        second_session_key = await _create_room_member(self.room.id, "Bob")
        alice_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        bob_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(second_session_key),
        )

        await self._connect_and_receive_initial_room_state(alice_socket)
        await self._connect_and_receive_initial_room_state(bob_socket)
        await self._receive_until_type(alice_socket, "room.state")

        round_id = await _start_game(self.room.id)
        round_started = await self._receive_until_type(alice_socket, "round.started")
        drawer_id = round_started["payload"]["drawer_participant_id"]

        if drawer_id == self.player.id:
            drawer_socket = alice_socket
            drawer_session_key = self.session_key
            observer_socket = bob_socket
        else:
            drawer_socket = bob_socket
            drawer_session_key = second_session_key
            observer_socket = alice_socket

        await drawer_socket.disconnect()

        # A round.state for the normal drawing phase may already be queued from
        # game start. Keep reading until the disconnect-grace state arrives.
        grace_state = None
        for _ in range(5):
            candidate_state = await self._receive_until_type(observer_socket, "round.state")
            if candidate_state["payload"]["status"] == "drawer_disconnected_grace":
                grace_state = candidate_state
                break
        self.assertIsNotNone(grace_state)
        self.assertEqual(grace_state["payload"]["status"], "drawer_disconnected_grace")
        self.assertTrue(grace_state["payload"].get("drawer_disconnect_deadline_at"))

        reconnect_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(drawer_session_key),
        )
        await self._connect_and_receive_initial_room_state(reconnect_socket)

        resumed_state = await self._receive_until_type(observer_socket, "round.state")
        self.assertEqual(resumed_state["payload"]["status"], "drawing")
        self.assertFalse(resumed_state["payload"].get("drawer_disconnect_deadline_at"))

        await asyncio.sleep(0.7)
        self.assertIsNone(await _get_round_status(round_id))

        await reconnect_socket.disconnect()
        await observer_socket.disconnect()

    @override_settings(
        SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True,
        SKETCHIT_ROUND_DURATION_SECONDS=5,
        SKETCHIT_INTERMISSION_DURATION_SECONDS=2,
        SKETCHIT_TIMER_TICK_INTERVAL_SECONDS=0.05,
        SKETCHIT_DRAWER_DISCONNECT_GRACE_SECONDS=0.3,
    )
    async def test_drawer_disconnect_grace_expiry_ends_round_as_drawer_disconnected(self):
        second_session_key = await _create_room_member(self.room.id, "Bob")
        alice_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        bob_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(second_session_key),
        )

        await self._connect_and_receive_initial_room_state(alice_socket)
        await self._connect_and_receive_initial_room_state(bob_socket)
        await self._receive_until_type(alice_socket, "room.state")

        round_id = await _start_game(self.room.id)
        round_started = await self._receive_until_type(alice_socket, "round.started")
        drawer_id = round_started["payload"]["drawer_participant_id"]

        if drawer_id == self.player.id:
            drawer_socket = alice_socket
            observer_socket = bob_socket
        else:
            drawer_socket = bob_socket
            observer_socket = alice_socket

        await drawer_socket.disconnect()

        grace_state = await self._receive_until_type(observer_socket, "round.state")
        self.assertEqual(grace_state["payload"]["status"], "drawer_disconnected_grace")
        self.assertTrue(grace_state["payload"].get("drawer_disconnect_deadline_at"))

        round_status = None
        for _ in range(40):
            round_status = await _get_round_status(round_id)
            if round_status == "drawer_disconnected":
                break
            await asyncio.sleep(0.05)
        self.assertEqual(round_status, "drawer_disconnected")

        await observer_socket.disconnect()

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

        await _connect_and_receive_initial_room_state(
            alice_primary_socket,
            self.room.join_code,
        )
        await _connect_and_receive_initial_room_state(
            bob_socket,
            self.room.join_code,
        )
        await _receive_until_type(alice_primary_socket, "room.state")

        alice_second_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await _connect_and_receive_initial_room_state(
            alice_second_socket,
            self.room.join_code,
        )

        self.assertTrue(await bob_socket.receive_nothing(timeout=0.2))

        await alice_second_socket.disconnect()
        await bob_socket.disconnect()
        await alice_primary_socket.disconnect()

    async def test_host_leave_broadcasts_host_changed_then_room_state(self):
        second_session_key = await _create_room_member(self.room.id, "Bob")
        bob_player = await database_sync_to_async(Player.objects.get)(
            room=self.room,
            session_key=second_session_key,
        )
        bob_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(second_session_key),
        )

        await _connect_and_receive_initial_room_state(
            bob_socket,
            self.room.join_code,
        )

        await _leave_room_member(
            redis_client=self.fake_redis,
            player_id=self.player.id,
        )

        host_changed = await bob_socket.receive_json_from(timeout=1)
        self.assertEqual(host_changed["type"], "host.changed")
        self.assertEqual(
            host_changed["payload"]["host"],
            {
                "id": bob_player.id,
                "display_name": bob_player.display_name,
            },
        )

        room_state = await bob_socket.receive_json_from(timeout=1)
        self.assertEqual(room_state["type"], "room.state")
        self.assertEqual(
            room_state["payload"]["host"],
            {
                "id": bob_player.id,
                "display_name": bob_player.display_name,
            },
        )
        self.assertEqual(
            room_state["payload"]["participants"],
            [
                {
                    "id": bob_player.id,
                    "display_name": bob_player.display_name,
                    "connection_status": Player.ConnectionStatus.CONNECTED,
                    "participation_status": bob_player.participation_status,
                    "current_score": bob_player.current_score,
                }
            ],
        )

        await bob_socket.disconnect()

    async def test_socket_reconnect_during_active_round_preserves_score(self):
        # A-07 reconnect reclaim: a non-drawer who drops their socket mid-game
        # and reconnects must land on the same Player row with their score
        # intact. Losing the score on reconnect would punish anyone whose
        # network blipped or whose tab refreshed during a round.
        @database_sync_to_async
        def _mark_game_in_progress_with_score():
            self.player.current_score = 42
            self.player.participation_status = Player.ParticipationStatus.PLAYING
            self.player.save(
                update_fields=[
                    "current_score",
                    "participation_status",
                    "updated_at",
                ],
            )
            self.room.status = Room.Status.IN_PROGRESS
            self.room.save(update_fields=["status", "updated_at"])

        await _mark_game_in_progress_with_score()
        original_player_id = self.player.id

        first_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await _connect_and_receive_initial_room_state(
            first_socket,
            self.room.join_code,
        )

        await first_socket.disconnect()

        # Confirm the disconnect did not wipe the stored score.
        await database_sync_to_async(self.player.refresh_from_db)()
        self.assertEqual(self.player.current_score, 42)
        self.assertEqual(
            self.player.participation_status,
            Player.ParticipationStatus.PLAYING,
        )

        reconnect_socket = WebsocketCommunicator(
            _TEST_APP,
            _ws_url(self.room.join_code),
            headers=_session_headers(self.session_key),
        )
        await _connect_and_receive_initial_room_state(
            reconnect_socket,
            self.room.join_code,
        )

        refreshed_player = await database_sync_to_async(Player.objects.get)(
            room=self.room,
            session_key=self.session_key,
        )
        # Same DB row, same score, same participation_status after reconnect.
        self.assertEqual(refreshed_player.id, original_player_id)
        self.assertEqual(refreshed_player.current_score, 42)
        self.assertEqual(
            refreshed_player.participation_status,
            Player.ParticipationStatus.PLAYING,
        )
        # And the reconnecting socket should now be back to CONNECTED.
        self.assertEqual(
            refreshed_player.connection_status,
            Player.ConnectionStatus.CONNECTED,
        )

        await reconnect_socket.disconnect()

    async def test_spectator_cannot_submit_guess(self):
        # A-07: a mid-game joiner (SPECTATING) must receive a guess.error with
        # a clear message instead of having their guess evaluated or silently
        # dropped. The service layer would also reject it, but the consumer
        # guard fires first and gives a friendlier, explicit response.
        #
        # SessionStore.save() and Player.objects.create() are synchronous DB
        # operations; both must be wrapped in database_sync_to_async when
        # called from inside an async test method.
        @database_sync_to_async
        def _create_spectator_session_and_player():
            spectator_session = SessionStore()
            spectator_session.save()
            player = Player.objects.create(
                room=self.room,
                session_key=spectator_session.session_key,
                display_name="Spectator",
                participation_status=Player.ParticipationStatus.SPECTATING,
                session_expires_at=spectator_session.get_expiry_date(),
            )
            return spectator_session.session_key, spectator_session.get_expiry_date(), player

        spectator_session_key, _, spectator = await _create_spectator_session_and_player()

        from games.models import Game, GameStatus, Round, GameWord
        game = await database_sync_to_async(Game.objects.create)(room=self.room, status=GameStatus.IN_PROGRESS)
        game_word = await database_sync_to_async(GameWord.objects.create)(game=game, text="rocket")
        round_obj = await database_sync_to_async(Round.objects.create)(
            game=game,
            drawer_participant=self.player,
            drawer_nickname=self.player.display_name,
            selected_game_word=game_word,
            sequence_number=1,
        )

        # Simulate an active round in Redis turn state so the no-active-round
        # guard doesn't fire before the spectator guard does.
        deadline_at = (timezone.now() + timedelta(seconds=60)).isoformat()
        from games import redis as game_redis
        await database_sync_to_async(game_redis.set_turn_state)(
            self.fake_redis,
            self.room.join_code,
            {
                "phase": "round",
                "status": "drawing",
                "game_id": str(game.id),
                "round_id": str(round_obj.id),
                "drawer_participant_id": str(self.player.id),
                "deadline_at": deadline_at,
                "eligible_guesser_ids": "[]",
                "correct_guesser_ids": "[]",
                "round_timer_sequence": "0",
                "intermission_timer_sequence": "0",
                "last_timer_server_timestamp": timezone.now().isoformat(),
            },
        )

        with override_settings(SKETCHIT_ENABLE_RUNTIME_COORDINATOR=True):
            communicator = WebsocketCommunicator(
                _TEST_APP,
                _ws_url(self.room.join_code),
                headers=_session_headers(spectator_session_key),
            )
            await _connect_and_drain_initial_sync(
                communicator,
                self.room.join_code,
                expects_game_active=True,
            )

            await communicator.send_json_to({
                "type": "guess.submit",
                "payload": {"text": "rocket"},
            })

            response = await communicator.receive_json_from(timeout=1)

        self.assertEqual(response["type"], "guess.error")
        self.assertEqual(
            response["payload"]["message"],
            "Spectators cannot submit guesses during the current round.",
        )
        await communicator.disconnect()


class RoomGroupNameTests(TransactionTestCase):
    """Tests for the room group naming helper."""

    async def test_group_name_uses_room_prefix(self):
        self.assertEqual(_room_group_name("ABC12345"), "room_ABC12345")

    async def test_group_name_is_stable_for_same_join_code(self):
        self.assertEqual(_room_group_name("TEST1234"), _room_group_name("TEST1234"))

    async def test_different_rooms_have_different_group_names(self):
        self.assertNotEqual(_room_group_name("ROOM0001"), _room_group_name("ROOM0002"))
