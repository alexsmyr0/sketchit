"""Room-domain service helpers for participant lifecycle transitions.

These helpers centralize participant state changes so views, WebSocket
consumers, and later background tasks all apply the same rules.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone

from core.realtime_groups import room_group_name
from games import redis as game_redis
from rooms import redis as room_redis
from rooms.models import Player, Room


logger = logging.getLogger(__name__)

EMPTY_ROOM_GRACE_PERIOD = timedelta(minutes=1)


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


def _cancel_non_resumable_active_games_for_room(*, room_id: int) -> None:
    """Cancel any in-progress games that became non-resumable after room emptying.

    The SDS says a room that emptied out must come back as a clean lobby on
    rejoin. That means any active game tied to the old room state must be
    marked cancelled instead of being silently resumed.
    """

    from games.models import Game, GameStatus, Round, RoundStatus

    cancelled_at = timezone.now()
    active_game_ids = list(
        Game.objects.select_for_update()
        .filter(room_id=room_id, status=GameStatus.IN_PROGRESS)
        .order_by("started_at", "id")
        .values_list("id", flat=True)
    )
    if not active_game_ids:
        return

    Round.objects.select_for_update().filter(
        game_id__in=active_game_ids,
        status__isnull=True,
        ended_at__isnull=True,
    ).update(
        status=RoundStatus.CANCELLED,
        ended_at=cancelled_at,
        updated_at=cancelled_at,
    )
    Game.objects.filter(
        id__in=active_game_ids,
        status=GameStatus.IN_PROGRESS,
    ).update(
        status=GameStatus.CANCELLED,
        ended_at=cancelled_at,
        updated_at=cancelled_at,
    )


def _delete_room_game_history(*, room_id: int) -> None:
    """Delete all game-owned records for one room in dependency-safe order.

    ``Round.selected_game_word`` uses ``PROTECT``, so deleting the room alone is
    not enough. We must remove dependent rows from the leaves inward.
    """

    from games.models import Game, GameWord, Guess, Round

    Guess.objects.filter(round__game__room_id=room_id).delete()
    Round.objects.filter(game__room_id=room_id).delete()
    GameWord.objects.filter(game__room_id=room_id).delete()
    Game.objects.filter(room_id=room_id).delete()


def _teardown_room_game_runtime(
    *,
    redis_client,
    join_code: str,
    include_cleanup_deadline: bool,
) -> None:
    """Delegate room-wide gameplay runtime cleanup to the games runtime module."""

    from games import runtime as game_runtime

    game_runtime.teardown_room_runtime(
        join_code,
        redis_client=redis_client,
        include_cleanup_deadline=include_cleanup_deadline,
    )


def _schedule_empty_room_cleanup_deadline_after_commit(
    *,
    redis_client,
    join_code: str,
    deadline_at: datetime,
) -> None:
    """Write the empty-room cleanup deadline only after the DB commit succeeds."""

    transaction.on_commit(
        lambda: _set_empty_room_cleanup_deadline(
            redis_client=redis_client,
            join_code=join_code,
            deadline_at=deadline_at,
        )
    )


def _schedule_restore_runtime_cleanup_after_commit(*, redis_client, join_code: str) -> None:
    """Clear empty-room runtime state only after the restore transaction commits."""

    transaction.on_commit(
        lambda: (
            _teardown_room_game_runtime(
                redis_client=redis_client,
                join_code=join_code,
                include_cleanup_deadline=False,
            ),
            _clear_empty_room_cleanup_deadline(
                redis_client=redis_client,
                join_code=join_code,
            ),
        )
    )


def _schedule_delete_runtime_cleanup_after_commit(*, redis_client, join_code: str) -> None:
    """Clear room runtime state only after the room delete has committed."""

    transaction.on_commit(
        lambda: _teardown_room_game_runtime(
            redis_client=redis_client,
            join_code=join_code,
            include_cleanup_deadline=True,
        )
    )


def _serialize_host_for_room_state(host: Player | None) -> dict | None:
    """Return the A-06 host payload for ``room.state`` and ``host.changed``."""

    if host is None:
        return None

    return {
        "id": host.id,
        "display_name": host.display_name,
    }


def _serialize_participant_for_room_state(player: Player) -> dict:
    """Return the A-06 participant payload for one room member."""

    return {
        "id": player.id,
        "display_name": player.display_name,
        "connection_status": player.connection_status,
        "participation_status": player.participation_status,
        "current_score": player.current_score,
    }


def _get_room_state_snapshot(*, room_id: int) -> dict:
    """Load the authoritative room snapshot used by A-06 live lobby events.

    ``room.state`` intentionally reuses the existing HTTP lobby JSON shape so
    the browser can consume one stable server-owned structure instead of
    merging separate lobby schemas for REST and WebSocket updates.
    """

    room = Room.objects.select_related("host").get(pk=room_id)
    participants = room.participants.order_by("created_at", "id")

    return {
        "room": {
            "name": room.name,
            "join_code": room.join_code,
            "visibility": room.visibility,
            "status": room.status,
        },
        "host": _serialize_host_for_room_state(room.host),
        "participants": [
            _serialize_participant_for_room_state(player)
            for player in participants
        ],
    }


def _build_room_state_event(*, room_id: int) -> dict:
    """Build the A-06 ``room.state`` event from the latest committed room data."""

    return {
        "type": "room.state",
        "payload": _get_room_state_snapshot(room_id=room_id),
    }


def _build_host_changed_event(*, host: Player | None) -> dict:
    """Build the A-06 ``host.changed`` event payload."""

    return {
        "type": "host.changed",
        "payload": {
            "host": _serialize_host_for_room_state(host),
        },
    }


def _publish_room_group_event(*, join_code: str, event: dict) -> None:
    """Publish one A-06 room-group event to every connected participant.

    Later batches will schedule these broadcasts with ``transaction.on_commit``
    so sockets only see state that actually committed to the database.
    """

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    async_to_sync(channel_layer.group_send)(
        room_group_name(join_code),
        {
            "type": "room.server_event",
            "event": event,
        },
    )


def schedule_room_state_broadcast_after_commit(*, join_code: str, room_id: int) -> None:
    """Broadcast the latest committed ``room.state`` snapshot after commit.

    The room snapshot is built inside the on-commit callback so the payload is
    derived from the database state that actually committed, not from in-memory
    model instances that might later roll back.
    """

    transaction.on_commit(
        lambda: _publish_room_group_event(
            join_code=join_code,
            event=_build_room_state_event(room_id=room_id),
        )
    )


def schedule_host_changed_broadcast_after_commit(
    *,
    join_code: str,
    host: Player | None,
) -> None:
    """Broadcast the committed A-06 ``host.changed`` event after commit."""

    transaction.on_commit(
        lambda: _publish_room_group_event(
            join_code=join_code,
            event=_build_host_changed_event(host=host),
        )
    )


def _schedule_drawer_reconnect_resume_after_commit(
    *,
    join_code: str,
    player_id: int,
) -> None:
    """Resume round runtime if an active drawer reconnects before grace expiry."""

    def _resume_drawer_round_if_needed() -> None:
        from games import runtime as game_runtime

        try:
            game_runtime.handle_participant_reconnected(
                join_code=join_code,
                participant_id=player_id,
            )
        except Exception:
            logger.exception(
                "Failed to resume drawer round after reconnect.",
                extra={
                    "join_code": join_code,
                    "player_id": player_id,
                },
            )

    transaction.on_commit(_resume_drawer_round_if_needed)


def _schedule_drawer_disconnect_grace_after_commit(
    *,
    join_code: str,
    player_id: int,
) -> None:
    """Start drawer-disconnect grace runtime if the active drawer dropped."""

    def _start_drawer_grace_if_needed() -> None:
        from games import runtime as game_runtime

        try:
            game_runtime.handle_participant_disconnected(
                join_code=join_code,
                participant_id=player_id,
            )
        except Exception:
            logger.exception(
                "Failed to start drawer disconnect grace window.",
                extra={
                    "join_code": join_code,
                    "player_id": player_id,
                },
            )

    transaction.on_commit(_start_drawer_grace_if_needed)


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
) -> bool:
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
    previous_connection_status = player.connection_status

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
    if previous_connection_status != Player.ConnectionStatus.CONNECTED:
        _schedule_drawer_reconnect_resume_after_commit(
            join_code=room_join_code,
            player_id=player.id,
        )
    # Multiple tabs for the same session share one room-level participant.
    # Returning whether the durable status changed lets the consumer broadcast
    # to existing peers after the DB commit without treating extra tabs as a
    # fake reconnect.
    status_changed = previous_connection_status != Player.ConnectionStatus.CONNECTED
    if status_changed:
        schedule_room_state_broadcast_after_commit(
            join_code=room_join_code,
            room_id=player.room_id,
        )

    return status_changed


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

    _schedule_empty_room_cleanup_deadline_after_commit(
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

    # A revived room must become a clean lobby, not a hidden continuation of
    # the abandoned game that was active before everyone left.
    _cancel_non_resumable_active_games_for_room(room_id=room.id)
    room.status = Room.Status.LOBBY
    room.empty_since = None
    room.save(update_fields=["status", "empty_since", "updated_at"])
    _schedule_restore_runtime_cleanup_after_commit(
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

    _delete_room_game_history(room_id=room.id)
    join_code = room.join_code
    room.delete()
    # Runtime teardown waits until commit so Redis/game runtime never move
    # ahead of a database delete that later rolls back.
    _schedule_delete_runtime_cleanup_after_commit(
        redis_client=redis_client,
        join_code=join_code,
    )
    return True


def purge_expired_participants(
    *,
    redis_client,
    now: datetime | None = None,
) -> int:
    """Permanently remove participants whose Django guest session has expired.

    This routes expiry cleanup through ``leave_participant`` so host handoff and
    empty-room grace behave exactly like any other permanent leave path.
    """

    current_time = now or timezone.now()
    expired_player_ids = list(
        Player.objects.filter(session_expires_at__lte=current_time)
        .order_by("session_expires_at", "id")
        .values_list("id", flat=True)
    )

    purged_count = 0
    for player_id in expired_player_ids:
        try:
            leave_participant(
                redis_client=redis_client,
                player_id=player_id,
            )
        except Player.DoesNotExist:
            # A concurrent cleanup may have already removed this participant.
            continue
        purged_count += 1

    return purged_count


def purge_expired_participants_for_session(
    *,
    redis_client,
    session_key: str,
    now: datetime | None = None,
) -> int:
    """Remove expired participants that belong to one guest session.

    The room-entry flow only needs to clean up stale ownership rows for the
    browser session making the current request. Keeping this helper
    session-scoped avoids doing unrelated expiry cleanup for other guests on
    the critical path of create/join requests.
    """

    current_time = now or timezone.now()
    expired_player_ids = list(
        Player.objects.filter(
            session_key=session_key,
            session_expires_at__lte=current_time,
        )
        .order_by("session_expires_at", "id")
        .values_list("id", flat=True)
    )

    purged_count = 0
    for player_id in expired_player_ids:
        try:
            # Route the delete through the normal leave path so host handoff,
            # empty-room grace, and runtime cleanup stay consistent.
            leave_participant(
                redis_client=redis_client,
                player_id=player_id,
            )
        except Player.DoesNotExist:
            # A concurrent request may have already removed this stale row.
            continue
        purged_count += 1

    return purged_count


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
    that same session leaves the room.  Temporary disconnects (page refresh,
    brief network loss) must not delete the participant row — the session
    needs to remain a valid room member so the next page load succeeds.

    Stale lobby rows left by sessions that never return are cleaned up lazily
    in the create/join views: when a session tries to enter a *different* room
    and its existing row is DISCONNECTED in a LOBBY room, that view calls
    ``leave_participant`` before proceeding.  This keeps the socket path simple
    and avoids the hard-refresh regression caused by eager deletion here.
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
    if (
        not connection_still_present
        and player.connection_status != Player.ConnectionStatus.DISCONNECTED
    ):
        schedule_room_state_broadcast_after_commit(
            join_code=room_join_code,
            room_id=player.room_id,
        )
        _schedule_drawer_disconnect_grace_after_commit(
            join_code=room_join_code,
            player_id=player.id,
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
    previous_host_id = room.host_id

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
    _schedule_drawer_disconnect_grace_after_commit(
        join_code=room.join_code,
        player_id=player.id,
    )
    player.delete()
    if room.host_id != previous_host_id:
        schedule_host_changed_broadcast_after_commit(
            join_code=room.join_code,
            host=room.host,
        )
    schedule_room_state_broadcast_after_commit(
        join_code=room.join_code,
        room_id=room.id,
    )

    # Empty-room grace starts only when membership truly reaches zero. A plain
    # socket disconnect is not enough because the participant row still exists
    # and the room must remain reclaimable by the same session.
    if not Player.objects.filter(room_id=room.id).exists():
        if room.status == Room.Status.IN_PROGRESS:
            # A8: once the final participant permanently leaves an active room,
            # the current game is no longer resumable. Cancel it here on the
            # leave path only; temporary disconnects must still follow the
            # separate reconnect/grace behavior.
            from games.services import cancel_active_game_for_room

            cancel_active_game_for_room(room.id)
        enter_empty_room_grace(
            redis_client=redis_client,
            room_id=room.id,
        )


def promote_mid_game_spectators_to_players(*, room_id: int) -> int:
    """Promote connected spectators in a room to playing status.

    Mid-game joiners are stored as SPECTATING so they cannot guess or draw
    during the turn they joined in (see A-07 in the join_room view). This
    function is called at each round transition — after one round ends and
    before the next drawer is chosen — so that waiting spectators graduate
    into the full eligible pool for the upcoming turn.

    The CONNECTED filter avoids a "ghost PLAYING" state: a player who did an
    HTTP join but never opened a socket (or who disconnected while spectating)
    should not silently graduate to PLAYING while they are offline, because
    downstream consumers combine ``participation_status=PLAYING`` with
    ``connection_status=CONNECTED`` to decide eligibility. Keeping offline
    spectators in SPECTATING also means they will be promoted naturally on a
    later round transition once they reconnect.

    Using a bulk UPDATE rather than per-row saves is intentional: the caller
    (game services) already holds a lock on the game row inside a transaction,
    so individual row locks here would be redundant overhead.

    Returns the number of participants that were promoted from SPECTATING to
    PLAYING, which lets the caller log or assert the promotion if needed.
    """

    promoted_count = Player.objects.filter(
        room_id=room_id,
        participation_status=Player.ParticipationStatus.SPECTATING,
        connection_status=Player.ConnectionStatus.CONNECTED,
    ).update(
        participation_status=Player.ParticipationStatus.PLAYING,
        updated_at=timezone.now(),
    )
    return promoted_count


def is_player_spectating(*, player_id: int) -> bool:
    """Return True if the given player currently has SPECTATING status.

    Single source of truth for "is this participant a spectator right now?".
    Both the socket consumer (guess submission gate) and the game runtime
    (sync-event role selection) need the same answer, and they must agree —
    if the rule ever extends beyond ``participation_status`` (e.g. a
    ``joined_at_round_id`` field) it should only change here.

    We re-query instead of trusting a cached Player instance because the
    participation_status can flip between the moment the socket connected and
    the moment the check runs (e.g. a round transition promoted the player
    while the socket was open).
    """

    return Player.objects.filter(
        pk=player_id,
        participation_status=Player.ParticipationStatus.SPECTATING,
    ).exists()
