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
from django.utils import timezone
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
import redis

from rooms.models import Player, Room
from rooms import redis as room_redis
from games import redis as game_redis


_redis_client = None

def get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(settings.REDIS_URL)
    return _redis_client


@database_sync_to_async
def _update_presence(
    player_id: int,
    join_code: str,
    session_key: str,
    connection_id: str,
    connected: bool,
) -> None:
    now = timezone.now()
    client = get_redis_client()
    if connected:
        Player.objects.filter(pk=player_id).update(
            connection_status=Player.ConnectionStatus.CONNECTED,
            last_seen_at=now,
        )
        room_redis.add_presence(
            client,
            join_code,
            session_key,
            connection_id=connection_id,
        )
    else:
        room_redis.remove_presence(
            client,
            join_code,
            session_key,
            connection_id=connection_id,
        )
        connection_still_present = room_redis.is_present(client, join_code, session_key)
        Player.objects.filter(pk=player_id).update(
            connection_status=(
                Player.ConnectionStatus.CONNECTED
                if connection_still_present
                else Player.ConnectionStatus.DISCONNECTED
            ),
            last_seen_at=now,
        )


def _room_group_name(join_code: str) -> str:
    """Return the channel group name for *join_code*."""
    return f"room_{join_code}"


@database_sync_to_async
def _resolve_room_and_player(
    join_code: str, session_key: str
) -> tuple["Room | None", "Player | None"]:
    """Look up the Room and the Player for the given session key.

    Returns ``(None, None)`` if the room does not exist.
    Returns ``(room, None)`` if the session is not a participant.
    Returns ``(room, player)`` on success.
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
        self.session_key: str = session_key

        await self.channel_layer.group_add(self.room_group, self.channel_name)
        await _update_presence(
            self.player.id,
            self.join_code,
            self.session_key,
            self.channel_name,
            connected=True,
        )
        await self.accept()
        await self._send_initial_canvas_snapshot()

    async def disconnect(self, code: int) -> None:
        """Remove this channel from the room group on disconnect."""
        if hasattr(self, "room_group"):
            await self.channel_layer.group_discard(self.room_group, self.channel_name)
        if hasattr(self, "player") and hasattr(self, "session_key"):
            await _update_presence(
                self.player.id,
                self.join_code,
                self.session_key,
                self.channel_name,
                connected=False,
            )

    async def receive_json(self, content: dict, **kwargs) -> None:
        """Handle inbound JSON events."""
        message_type = content.get("type")
        
        if message_type == "echo":
            await self.send_json({
                "type": "echo_reply",
                "message": f"Echo: {content.get('message', '')}"
            })
        elif message_type in ("drawing.stroke", "drawing.end_stroke", "drawing.clear"):
            await self._handle_drawing_event(content)

    async def _handle_drawing_event(self, content: dict) -> None:
        """Process and broadcast drawer-authorized drawing events."""
        if not await self._is_active_drawer():
            # Silently ignore unauthorized drawing attempts
            return

        message_type = content.get("type")
        payload = content.get("payload", {})

        # Broadcast the event to all other room participants
        await self.channel_layer.group_send(
            self.room_group,
            {
                "type": "drawing_broadcast",
                "sender_channel_name": self.channel_name,
                "drawing_type": message_type,
                "payload": payload,
            }
        )

        # Update Redis canvas snapshot
        await self._update_redis_snapshot(message_type, payload)

    async def drawing_broadcast(self, event: dict) -> None:
        """Relay a drawing event broadcast to the connected WebSocket client."""
        # Don't echo drawing events back to the original sender
        if self.channel_name == event.get("sender_channel_name"):
            return

        await self.send_json({
            "type": event["drawing_type"],
            "payload": event["payload"],
        })

    @database_sync_to_async
    def _is_active_drawer(self) -> bool:
        """Return True if the connected player is the active drawer."""
        # 1. Room must be in progress
        # Since self.room is an ORM object from connect(), we should refresh or trust logic.
        # For N-04, we'll fetch the status from DB to be safe or just trust the connect-time snapshot.
        # Actually, let's refresh to be authoritative.
        self.room.refresh_from_db()
        if self.room.status != Room.Status.IN_PROGRESS:
            return False

        # 2. Match player ID against Redis turn state
        client = get_redis_client()
        turn_state = game_redis.get_turn_state(client, self.join_code)
        drawer_id_str = turn_state.get("drawer_id")
        
        if not drawer_id_str:
            return False
            
        return self.player.id == int(drawer_id_str)

    @database_sync_to_async
    def _update_redis_snapshot(self, message_type: str, payload: dict) -> None:
        """Update or clear the canvas snapshot in Redis."""
        client = get_redis_client()
        if message_type == "drawing.clear":
            room_redis.clear_canvas_snapshot(client, self.join_code)
        else:
            # We assume the payload contains the snapshot or data needed for it.
            # Convert payload to bytes for room_redis.set_canvas_snapshot.
            snapshot_data = json.dumps(payload).encode()
            room_redis.set_canvas_snapshot(client, self.join_code, snapshot_data)

    async def _send_initial_canvas_snapshot(self) -> None:
        """Send the current canvas state to a newly connected client."""
        snapshot = await self._get_redis_snapshot()
        if snapshot:
            try:
                # Assuming the snapshot was a JSON string encoded as bytes
                payload = json.loads(snapshot.decode())
                await self.send_json({
                    "type": "drawing.snapshot",
                    "payload": payload,
                })
            except (json.JSONDecodeError, UnicodeDecodeError):
                # If it's not JSON, skip or send as raw? SDS says snapshot is for sync.
                pass

    @database_sync_to_async
    def _get_redis_snapshot(self) -> bytes | None:
        return room_redis.get_canvas_snapshot(get_redis_client(), self.join_code)
