"""Room-domain service helpers for participant lifecycle transitions.

These helpers centralize participant state changes so views, WebSocket
consumers, and later background tasks all apply the same rules.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from games import redis as game_redis
from rooms import redis as room_redis
from rooms.models import Player, Room


EMPTY_ROOM_GRACE_PERIOD = timedelta(minutes=10)


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


def _get_locked_room(room_id: int) -> Room:
    """Return the room row locked for update.

    Room lifecycle writes such as host handoff and empty-room grace must lock
    the row so concurrent updates cannot overwrite each other.
    """

    return Room.objects.select_for_update().get(pk=room_id)


def get_empty_room_cleanup_deadline(*, empty_since: datetime) -> datetime:
    """Return the hard-delete deadline for a room already in empty grace.

    The deadline is derived from the durable ``empty_since`` timestamp so the
    cleanup policy survives process restarts and Redis cache loss.
    """

    return empty_since + EMPTY_ROOM_GRACE_PERIOD


def _set_empty_room_cleanup_deadline(*, redis_client, join_code: str, deadline_at: datetime) -> None:
    """Mirror the empty-room cleanup deadline into Redis runtime state."""

    game_redis.set_deadline(
        redis_client,
        join_code,
        "cleanup",
        deadline_at.isoformat(),
    )


def _clear_empty_room_cleanup_deadline(*, redis_client, join_code: str) -> None:
    """Remove the Redis-side empty-room cleanup deadline for one room."""

    game_redis.clear_deadline(redis_client, join_code, "cleanup")


def _validate_room_presence_identity(
    *,
    player: Player,
    join_code: str,
    session_key: str,
) -> None:
    """Reject presence updates that do not match the participant identity.

    The consumer already resolved the player from the room + session pair, but
    this guard keeps the service honest if another caller wires it incorrectly.
    That matters because presence is keyed by room/session identity; updating
    the wrong participant would corrupt both Redis and the durable row state.
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
    # Always use the canonical join code from the database row when touching
    # Redis so callers cannot accidentally split presence across differently
    # cased keys like "ABCD1234" and "abcd1234".
    room_join_code = player.room.join_code

    room_redis.add_presence(
        redis_client,
        room_join_code,
        session_key,
        connection_id=connection_id,
    )
    Player.objects.filter(pk=player.id).update(
        connection_status=Player.ConnectionStatus.CONNECTED,
        last_seen_at=timezone.now(),
    )


@transaction.atomic
def enter_empty_room_grace(
    *,
    redis_client,
    room_id: int,
    now: datetime | None = None,
) -> Room:
    """Move an already-empty room into its 10-minute grace window.

    Callers must use this only after they have removed the final participant.
    The service refuses non-empty rooms because resetting the grace timer for a
    room that still has members would hide a real lifecycle bug.
    """

    room = _get_locked_room(room_id)
    if room.participants.exists():
        raise ValueError("Cannot move a non-empty room into empty grace.")

    # Re-entering the service for a room already in grace must not extend the
    # deadline. We always preserve the first durable timestamp if it exists.
    empty_since = room.empty_since or now or timezone.now()
    room.status = Room.Status.EMPTY_GRACE
    room.empty_since = empty_since
    room.save(update_fields=["status", "empty_since", "updated_at"])

    _set_empty_room_cleanup_deadline(
        redis_client=redis_client,
        join_code=room.join_code,
        deadline_at=get_empty_room_cleanup_deadline(empty_since=empty_since),
    )
    return room


@transaction.atomic
def restore_room_from_empty_grace(*, redis_client, room_id: int) -> Room:
    """Return an empty-grace room to the lobby and clear cleanup timing.

    Batch 2 will call this when a participant rejoins before the deadline.
    Restricting it to ``empty_grace`` rooms keeps later callers from silently
    rewriting unrelated room states such as ``in_progress``.
    """

    room = _get_locked_room(room_id)
    if room.status != Room.Status.EMPTY_GRACE:
        raise ValueError("Only rooms in empty grace can be restored.")

    room.status = Room.Status.LOBBY
    room.empty_since = None
    room.save(update_fields=["status", "empty_since", "updated_at"])
    _clear_empty_room_cleanup_deadline(
        redis_client=redis_client,
        join_code=room.join_code,
    )
    return room


@transaction.atomic
def delete_room_if_empty_grace_expired(
    *,
    redis_client,
    room_id: int,
    now: datetime | None = None,
) -> bool:
    """Hard-delete an empty-grace room once its deadline has passed.

    Returns ``True`` only when the room was actually deleted. Returning a
    boolean keeps future cleanup jobs simple: they can count deletions without
    needing to infer intent from exceptions.
    """

    try:
        room = _get_locked_room(room_id)
    except Room.DoesNotExist:
        # Cleanup workers may race with each other or with a join path that
        # already deleted the expired room. Missing rows are therefore a
        # no-op, not an error.
        return False

    if room.status != Room.Status.EMPTY_GRACE or room.empty_since is None:
        return False

    if room.participants.exists():
        return False

    current_time = now or timezone.now()
    deadline_at = get_empty_room_cleanup_deadline(empty_since=room.empty_since)
    if current_time < deadline_at:
        return False

    join_code = room.join_code
    room.delete()
    _clear_empty_room_cleanup_deadline(
        redis_client=redis_client,
        join_code=join_code,
    )
    return True


def cleanup_expired_empty_rooms(
    *,
    redis_client,
    now: datetime | None = None,
) -> int:
    """Delete every room whose empty-grace deadline has already expired.

    The command path uses one shared ``now`` timestamp for the whole sweep so
    every candidate is evaluated against the same cutoff.
    """

    current_time = now or timezone.now()
    expired_room_ids = list(
        Room.objects.filter(
            status=Room.Status.EMPTY_GRACE,
            empty_since__isnull=False,
            empty_since__lte=current_time - EMPTY_ROOM_GRACE_PERIOD,
        )
        .order_by("empty_since", "id")
        .values_list("id", flat=True)
    )

    deleted_count = 0
    for room_id in expired_room_ids:
        if delete_room_if_empty_grace_expired(
            redis_client=redis_client,
            room_id=room_id,
            now=current_time,
        ):
            deleted_count += 1

    return deleted_count


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
    that same session leaves the room. Temporary disconnects should not delete
    the participant row or trigger host reassignment; those behaviors belong to
    a permanent leave only.
    """

    player = _get_locked_player(player_id)
    _validate_room_presence_identity(
        player=player,
        join_code=join_code,
        session_key=session_key,
    )
    room_join_code = player.room.join_code

    room_redis.remove_presence(
        redis_client,
        room_join_code,
        session_key,
        connection_id=connection_id,
    )
    # Redis knows about individual sockets, while MySQL stores the room-level
    # answer of whether this participant is still connected anywhere right now.
    connection_still_present = room_redis.is_present(
        redis_client,
        room_join_code,
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
    deleted, any tracked room presence for that session is cleared, and host
    ownership is reassigned if the departing participant was the current host.
    """

    player = _get_locked_player(player_id)
    room = _get_locked_room(player.room_id)

    if room.host_id == player.id:
        remaining_participants = list(
            Player.objects.select_for_update()
            .filter(room_id=room.id)
            .exclude(pk=player.id)
            .order_by("created_at", "id")
        )
        # Host handoff happens only on permanent leave. A plain disconnect keeps
        # the same host so temporary network loss does not silently transfer
        # lobby ownership to somebody else.
        #
        # The PRD/SDS require a random remaining participant to become host.
        room.host = random.choice(remaining_participants) if remaining_participants else None
        room.save(update_fields=["host", "updated_at"])

    # Leaving should clear every active socket for that session so the room's
    # live presence state does not keep a ghost participant around.
    room_redis.clear_session_presence(
        redis_client,
        player.room.join_code,
        player.session_key,
    )
    player.delete()

    # Empty-room grace starts only when membership truly reaches zero. A plain
    # socket disconnect is not enough because the participant row still exists
    # and the room must remain reclaimable by the same session.
    if not Player.objects.filter(room_id=room.id).exists():
        enter_empty_room_grace(
            redis_client=redis_client,
            room_id=room.id,
        )
