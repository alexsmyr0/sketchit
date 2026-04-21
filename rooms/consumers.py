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

import json
import logging
import redis

from django.conf import settings
from django.utils import timezone
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from core.realtime_groups import player_group_name, room_group_name
from rooms.models import Player, Room
from rooms.services import connect_participant, disconnect_participant
from rooms import redis as room_redis
from games import redis as game_redis
from games import services as game_services
from games.models import Round

logger = logging.getLogger(__name__)

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


def reset_redis_client() -> None:
    """Reset the cached Redis client. Used for test isolation."""
    global _redis_client
    _redis_client = None


@database_sync_to_async
def _mark_participant_connected(
    player_id: int,
    join_code: str,
    session_key: str,
    connection_id: str,
) -> bool:
    """Delegate socket-connect lifecycle updates to the room service."""

    return connect_participant(
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
def _get_connected_peer_ids(room_id: int, player_id: int) -> list[int]:
    """Return other currently connected participant ids for one room."""

    return list(
        Player.objects.filter(
            room_id=room_id,
            connection_status=Player.ConnectionStatus.CONNECTED,
        )
        .exclude(pk=player_id)
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )


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

        # The lifecycle service call below marks the participant connected in
        # Redis/MySQL and may schedule a room-wide ``room.state`` broadcast.
        # We therefore wait to join the room group until after ``accept()`` and
        # use the returned flag to broadcast only to already-connected peers.
        connection_state_changed = await _mark_participant_connected(
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
        initial_room_state_event = await _get_initial_room_state_event(self.room.id)
        await self.send_json(initial_room_state_event)

        await self.channel_layer.group_add(self.room_group, self.channel_name)
        await self.channel_layer.group_add(self.player_group, self.channel_name)

        if connection_state_changed:
            # Existing peers still need the connect/reconnect ``room.state``
            # update, but the connecting client already received the same
            # snapshot directly above. Fan-out via each peer's player group so
            # the newly connected socket is fully subscribed before connect()
            # returns without receiving a duplicate lobby snapshot.
            for peer_id in await _get_connected_peer_ids(self.room.id, self.player.id):
                await self.channel_layer.group_send(
                    _player_group_name(self.join_code, peer_id),
                    {
                        "type": "room.server_event",
                        "event": initial_room_state_event,
                    },
                )

        await self._send_initial_canvas_snapshot()
        for event in await _get_runtime_sync_events(self.join_code, self.player.id):
            await self.send_json(event)

    async def disconnect(self, code: int) -> None:
        """Remove this socket from channel groups and update lifecycle state.
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
        """Handle inbound JSON events."""
        message_type = content.get("type")

        if message_type == "echo":
            await self.send_json({
                "type": "echo_reply",
                "message": f"Echo: {content.get('message', '')}"
            })
        elif message_type == "round.sync_request":
            for event in await _get_runtime_sync_events(self.join_code, self.player.id):
                await self.send_json(event)
        elif message_type in ("drawing.stroke", "drawing.end_stroke", "drawing.clear"):
            await self._handle_drawing_event(content)
        elif message_type == "guess.submit":
            await self._handle_guess_submission(content)

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

    async def room_server_event(self, event: dict) -> None:
        """Forward room-group server events to this socket client."""
        server_event = event.get("event")
        if not isinstance(server_event, dict):
            return
        await self.send_json(server_event)

    @database_sync_to_async
    def _is_active_drawer(self) -> bool:
        """Return True if the connected player is the active drawer.

        This check is performed against Redis turn state to avoid database
        load during high-frequency drawing events.
        """
        # 1. Match player ID against Redis turn state
        client = get_redis_client()
        turn_state = game_redis.get_turn_state(client, self.join_code)

        # turn_state is only populated when a round is active.
        # Drawer authorization field name from K-03 architecture: drawer_participant_id
        drawer_participant_id = turn_state.get("drawer_participant_id")

        if not drawer_participant_id:
            return False

        return self.player.id == int(drawer_participant_id)

    @database_sync_to_async
    def _is_spectator(self) -> bool:
        """Return True if the connected player is currently a spectator.

        Delegates to ``rooms.services.is_player_spectating`` so the rule
        (what counts as "spectator") lives in exactly one place; see the
        docstring there for why we re-query instead of trusting self.player.
        """
        from rooms.services import is_player_spectating

        return is_player_spectating(player_id=self.player.id)

    @database_sync_to_async
    def _update_redis_snapshot(self, message_type: str, payload: dict) -> None:
        """Update or clear the canvas snapshot in Redis."""
        client = get_redis_client()
        if message_type == "drawing.clear":
            room_redis.clear_canvas_snapshot(client, self.join_code)
        else:
            # message_type is drawing.stroke or drawing.end_stroke.
            # We wrap the payload with its type for semantic reconstruction by late joiners.
            data = {
                "type": message_type,
                "payload": payload
            }
            snapshot_data = json.dumps(data).encode()
            room_redis.append_canvas_stroke(client, self.join_code, snapshot_data)

    async def _send_initial_canvas_snapshot(self) -> None:
        """Send the current canvas state to a newly connected client.

        Only sends if there is an active round in progress.
        """
        # Check if a round is active before sending snapshot
        client = get_redis_client()
        turn_state = await database_sync_to_async(game_redis.get_turn_state)(client, self.join_code)
        if not turn_state:
            return

        snapshots = await self._get_redis_snapshot()
        for snapshot_bytes in snapshots:
            try:
                event_data = json.loads(snapshot_bytes.decode())
                await self.send_json(event_data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.warning(f"Corrupted drawing snapshot in room {self.join_code}")

    @database_sync_to_async
    def _get_redis_snapshot(self) -> list[bytes]:
        return room_redis.get_canvas_snapshot(get_redis_client(), self.join_code)

    async def _handle_guess_submission(self, content: dict) -> None:
        """Process a guess submission from a participant."""
        payload = content.get("payload", {})
        guess_text = payload.get("text", "").strip()
        if not guess_text:
            await self.send_json({
                "type": "guess.error",
                "payload": {
                    "message": "Guess text cannot be empty.",
                    "server_timestamp": timezone.now().isoformat()
                }
            })
            return

        # 1. Fetch active round from Redis turn state
        client = get_redis_client()
        turn_state = await database_sync_to_async(game_redis.get_turn_state)(client, self.join_code)
        round_id_str = turn_state.get("round_id")

        if not round_id_str:
            await self.send_json({
                "type": "guess.error",
                "payload": {
                    "message": "No active round in progress.",
                    "server_timestamp": timezone.now().isoformat()
                }
            })
            return

        # 2. Prevent drawer from guessing (avoid DB pollution)
        if await self._is_active_drawer():
            await self.send_json({
                "type": "guess.error",
                "payload": {
                    "message": "Drawers cannot submit guesses for their own round.",
                    "server_timestamp": timezone.now().isoformat()
                }
            })
            return

        # A-07: spectators joined after the game started and are not eligible
        # to guess until the next round promotes them to PLAYING. Checking here
        # gives a clear, user-facing message rather than a generic service error.
        if await self._is_spectator():
            await self.send_json({
                "type": "guess.error",
                "payload": {
                    "message": "Spectators cannot submit guesses during the current round.",
                    "server_timestamp": timezone.now().isoformat()
                }
            })
            return

        # 3. Evaluate the guess via the game service
        try:
            active_round = await self._resolve_round(int(round_id_str))
            if not active_round:
                await self.send_json({
                    "type": "guess.error",
                    "payload": {
                        "message": "The targeted round has already completed.",
                        "server_timestamp": timezone.now().isoformat()
                    }
                })
                return

            evaluation_result = await database_sync_to_async(game_services.evaluate_guess_for_round)(
                active_round, self.player, guess_text
            )

            # 4. Broadcast result to all participants
            await self.channel_layer.group_send(
                self.room_group,
                {
                    "type": "guess_broadcast",
                    "player_id": self.player.id,
                    "player_nickname": self.player.display_name,
                    "text": guess_text,
                    "outcome": evaluation_result.outcome,
                    "is_correct": evaluation_result.is_correct,
                    "round_completed": evaluation_result.round_completed,
                    "score_updates": [
                        {"player_id": s.player_id, "current_score": s.current_score}
                        for s in evaluation_result.score_updates
                    ]
                }
            )
        except game_services.GuessEvaluationError as e:
            await self.send_json({
                "type": "guess.error",
                "payload": {
                    "message": str(e),
                    "server_timestamp": timezone.now().isoformat()
                }
            })
        except Exception:
            logger.exception(f"Error evaluating guess from player {self.player.id} in room {self.join_code}")
            await self.send_json({
                "type": "guess.error",
                "payload": {
                    "message": "An unexpected error occurred while processing your guess.",
                    "server_timestamp": timezone.now().isoformat()
                }
            })

    async def guess_broadcast(self, event: dict) -> None:
        """Relay a guess result broadcast to the connected WebSocket client."""
        await self.send_json({
            "type": "guess.result",
            "payload": {
                "player_id": event["player_id"],
                "player_nickname": event["player_nickname"],
                "text": event["text"],
                "outcome": event["outcome"],
                "is_correct": event["is_correct"],
                "round_completed": event["round_completed"],
                "score_updates": event["score_updates"]
            }
        })

    @database_sync_to_async
    def _resolve_round(self, round_id: int) -> Round | None:
        return Round.objects.filter(pk=round_id, status__isnull=True).select_related("game__room", "selected_game_word").first()
