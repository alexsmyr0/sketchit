"""
WebSocket consumer for room-scoped real-time communication.

A single RoomConsumer instance handles one WebSocket connection to a room.
On connect it resolves the room from the URL join_code, validates that the
connecting Django session belongs to that room as a participant, then adds the
channel to a stable room-scoped group.

Later issues (#29, #30, #31) will add event handlers on top of this skeleton.

Group naming
------------
All connections to the same room share one channel group named
``room_{join_code}``.  Broadcasting to this group reaches every currently
connected participant.

Close codes
-----------
4001  No session key found in the request (unauthenticated guest).
4003  Session is not a participant in the target room (forbidden).
4004  Room does not exist (not found).
"""

from django.conf import settings
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from core.realtime_groups import player_group_name, room_group_name
import redis

from rooms.models import Player, Room
from rooms.services import connect_participant, disconnect_participant


_redis_client = None


def get_redis_client() -> redis.Redis:
    """Return a cached Redis client for room runtime state.

    Consumers are created per socket connection, so caching avoids rebuilding
    the Redis client every time a participant connects or disconnects.
    """

    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(settings.REDIS_URL)
    return _redis_client


@database_sync_to_async
def _mark_participant_connected(
    player_id: int,
    join_code: str,
    session_key: str,
    connection_id: str,
) -> None:
    """Delegate socket-connect lifecycle updates to the room service."""

    connect_participant(
        redis_client=get_redis_client(),
        player_id=player_id,
        join_code=join_code,
        session_key=session_key,
        connection_id=connection_id,
    )


@database_sync_to_async
def _mark_participant_disconnected(
    player_id: int,
    join_code: str,
    session_key: str,
    connection_id: str,
) -> None:
    """Delegate socket-disconnect lifecycle updates to the room service."""

    disconnect_participant(
        redis_client=get_redis_client(),
        player_id=player_id,
        join_code=join_code,
        session_key=session_key,
        connection_id=connection_id,
    )


def _room_group_name(join_code: str) -> str:
    """Return the channel group name for *join_code*."""
    return room_group_name(join_code)


def _player_group_name(join_code: str, player_id: int) -> str:
    """Return the per-player channel group for one room participant."""
    return player_group_name(join_code, player_id)


@database_sync_to_async
def _resolve_room_and_player(
    join_code: str, session_key: str
) -> tuple["Room | None", "Player | None"]:
    """Look up the Room and the Player for the given session key.

    Returns ``(None, None)`` if the room does not exist.
    Returns ``(room, None)`` if the session is not a participant.
    Returns ``(room, player)`` on success.

    Reconnect reuse stays simple: if the same Django session reconnects, we
    resolve the already-owned participant row instead of creating a new one.
    """
    try:
        room = Room.objects.get(join_code=join_code.upper())
    except Room.DoesNotExist:
        return None, None

    player = (
        Player.objects.filter(room=room, session_key=session_key)
        .select_related("room")
        .first()
    )
    return room, player


@database_sync_to_async
def _get_runtime_sync_events(join_code: str, player_id: int) -> list[dict]:
    """Return server-authoritative runtime sync events for one participant."""
    from games import runtime as game_runtime

    return game_runtime.get_sync_events_for_player(join_code, player_id)


@database_sync_to_async
def _get_initial_room_state_event(room_id: int) -> dict:
    """Return the direct A-06 ``room.state`` snapshot for one socket connect.

    Reusing the room-service event builder keeps the direct post-connect
    snapshot identical to the room-group broadcasts used for later lobby
    updates.
    """

    from rooms.services import _build_room_state_event

    return _build_room_state_event(room_id=room_id)


class RoomConsumer(AsyncJsonWebsocketConsumer):
    """Session-aware WebSocket consumer for a single room.

    Instance attributes set after a successful connect
    --------------------------------------------------
    self.join_code   – normalised uppercase join code (e.g. "ABC12345")
    self.room        – the Room ORM instance
    self.player      – the Player ORM instance for the connected session
    self.room_group  – channel group name (e.g. "room_ABC12345")
    """

    async def connect(self) -> None:
        self.join_code: str = self.scope["url_route"]["kwargs"]["join_code"].upper()

        # AuthMiddlewareStack populates scope["session"] from the session cookie.
        session = self.scope.get("session")
        session_key: str | None = getattr(session, "session_key", None) if session else None

        if not session_key:
            # No valid Django session — guest is not identified.
            await self.close(code=4001)
            return

        room, player = await _resolve_room_and_player(self.join_code, session_key)

        if room is None:
            await self.close(code=4004)
            return

        if player is None:
            # Session exists but this guest is not a participant in the room.
            await self.close(code=4003)
            return

        self.room: Room = room
        self.player: Player = player
        self.room_group: str = _room_group_name(self.join_code)
        self.player_group: str = _player_group_name(self.join_code, self.player.id)
        self.session_key: str = session_key

        # Group membership controls future fan-out delivery. The lifecycle
        # service call below is the separate step that marks the participant as
        # connected in Redis/MySQL.
        await self.channel_layer.group_add(self.room_group, self.channel_name)
        await self.channel_layer.group_add(self.player_group, self.channel_name)
        await _mark_participant_connected(
            self.player.id,
            self.join_code,
            self.session_key,
            self.channel_name,
        )
        await self.accept()

        # The socket must be accepted before we try to send frames back to the
        # browser; the direct snapshot therefore belongs after ``accept()``.
        #
        # This direct send is separate from room-group fan-out because the
        # connecting client must not depend on timing of any room-wide
        # broadcast to receive its initial authoritative lobby state.
        await self.send_json(await _get_initial_room_state_event(self.room.id))

        # A reconnecting/late-joining client needs an immediate phase snapshot
        # after the lobby snapshot so timer UI does not depend on waiting for
        # the next periodic tick.
        for event in await _get_runtime_sync_events(self.join_code, self.player.id):
            await self.send_json(event)

    async def disconnect(self, code: int) -> None:
        """Remove this socket from channel groups and update lifecycle state.

        Disconnect only closes this one socket. The service layer decides
        whether the participant still counts as connected overall, because the
        same guest session may still have another open tab/socket in the room.
        """
        if hasattr(self, "room_group"):
            await self.channel_layer.group_discard(self.room_group, self.channel_name)
        if hasattr(self, "player_group"):
            await self.channel_layer.group_discard(self.player_group, self.channel_name)
        if hasattr(self, "player") and hasattr(self, "session_key"):
            await _mark_participant_disconnected(
                self.player.id,
                self.join_code,
                self.session_key,
                self.channel_name,
            )

    async def receive_json(self, content: dict, **kwargs) -> None:
        """Handle inbound JSON events.

        Extended by later issues — placeholder for now, but provides a basic
        echo for initial real-time communication testing (Issue #12).
        """
        message_type = content.get("type")
        
        if message_type == "echo":
            await self.send_json({
                "type": "echo_reply",
                "message": f"Echo: {content.get('message', '')}"
            })
            return

        if message_type == "round.sync_request":
            for event in await _get_runtime_sync_events(self.join_code, self.player.id):
                await self.send_json(event)
            return

    async def room_server_event(self, event: dict) -> None:
        """Forward room-group server events to this socket client."""
        server_event = event.get("event")
        if not isinstance(server_event, dict):
            return
        await self.send_json(server_event)
