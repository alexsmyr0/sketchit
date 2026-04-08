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

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from rooms.models import Player, Room


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

        await self.channel_layer.group_add(self.room_group, self.channel_name)
        await self.accept()

    async def disconnect(self, code: int) -> None:
        """Remove this channel from the room group on disconnect."""
        if hasattr(self, "room_group"):
            await self.channel_layer.group_discard(self.room_group, self.channel_name)

    async def receive_json(self, content: dict, **kwargs) -> None:
        """Handle inbound JSON events.

        Extended by later issues — placeholder for now.
        """
        pass
