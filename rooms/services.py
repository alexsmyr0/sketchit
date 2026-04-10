"""Room-domain service helpers for participant lifecycle transitions.

These helpers centralize participant state changes so views, WebSocket
consumers, and later background tasks all apply the same rules.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from rooms import redis as room_redis
from rooms.models import Player


def _get_locked_player(player_id: int) -> Player:
    """Return the participant row locked for update.

    We lock the row so concurrent connect/disconnect operations for the same
    participant do not race while we mirror aggregate presence into MySQL.
    """

    return (
        Player.objects.select_for_update()
        .select_related("room")
        .get(pk=player_id)
    )


def _validate_room_presence_identity(
    *,
    player: Player,
    join_code: str,
    session_key: str,
) -> None:
    """Reject presence updates that do not match the participant identity.

    The consumer already resolved the player from the room + session pair, but
    this guard keeps the service honest if another caller wires it incorrectly.
    """

    if player.room.join_code != join_code.upper():
        raise ValueError("Participant does not belong to the given room join code.")

    if player.session_key != session_key:
        raise ValueError("Participant does not belong to the given session key.")


@transaction.atomic
def connect_participant(
    *,
    redis_client,
    player_id: int,
    join_code: str,
    session_key: str,
    connection_id: str,
) -> None:
    """Mark one participant as connected for an active socket.

    Redis tracks the per-socket presence details. MySQL stores the durable
    aggregate view that the participant is currently connected.
    """

    player = _get_locked_player(player_id)
    _validate_room_presence_identity(
        player=player,
        join_code=join_code,
        session_key=session_key,
    )

    room_redis.add_presence(
        redis_client,
        join_code,
        session_key,
        connection_id=connection_id,
    )
    Player.objects.filter(pk=player.id).update(
        connection_status=Player.ConnectionStatus.CONNECTED,
        last_seen_at=timezone.now(),
    )


@transaction.atomic
def disconnect_participant(
    *,
    redis_client,
    player_id: int,
    join_code: str,
    session_key: str,
    connection_id: str,
) -> None:
    """Mark one participant socket as disconnected.

    A participant only becomes fully disconnected after the final socket for
    that same session leaves the room.
    """

    player = _get_locked_player(player_id)
    _validate_room_presence_identity(
        player=player,
        join_code=join_code,
        session_key=session_key,
    )

    room_redis.remove_presence(
        redis_client,
        join_code,
        session_key,
        connection_id=connection_id,
    )
    connection_still_present = room_redis.is_present(
        redis_client,
        join_code,
        session_key,
    )
    Player.objects.filter(pk=player.id).update(
        connection_status=(
            Player.ConnectionStatus.CONNECTED
            if connection_still_present
            else Player.ConnectionStatus.DISCONNECTED
        ),
        last_seen_at=timezone.now(),
    )


@transaction.atomic
def leave_participant(*, redis_client, player_id: int) -> None:
    """Remove a participant from the room entirely.

    This is different from a temporary disconnect: the membership row is
    deleted, and any tracked room presence for that session is cleared too.
    Host reassignment rules are handled in the later A04 host-handoff batch.
    """

    player = _get_locked_player(player_id)

    # Leaving should clear every active socket for that session so the room's
    # live presence state does not keep a ghost participant around.
    room_redis.clear_session_presence(
        redis_client,
        player.room.join_code,
        player.session_key,
    )
    player.delete()
