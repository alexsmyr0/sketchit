"""
Redis-side helpers for temporary room state.

This module owns all Redis key definitions and operations for the rooms app.
It is intentionally dependency-free: callers pass in a redis.Redis (or
compatible) client so the functions can be used with any client instance and
are trivially testable with fakeredis.

Key layout
----------
room:{join_code}:presence                               Redis Set
    Connected session keys for a room.
room:{join_code}:presence:{session_key}:connections     Redis Set
    Active socket/channel IDs for one session in a room.
room:{join_code}:canvas                                 Redis String
    Latest canvas snapshot bytes for a room.

Both keys carry a 24-hour TTL that is refreshed on every write.  This ensures
orphaned keys are cleaned up automatically if the server never calls the
explicit clear helpers (e.g. after an unexpected restart).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Avoid a hard import at module level so this file can be imported even
    # when the redis package is not installed (e.g. in some CI environments).
    import redis as _redis

# ---------------------------------------------------------------------------
# TTL
# ---------------------------------------------------------------------------

#: How long presence / canvas keys survive without any write activity.
ROOM_KEY_TTL = 60 * 60 * 24  # 24 hours in seconds


# ---------------------------------------------------------------------------
# Internal key builders
# ---------------------------------------------------------------------------


def _presence_key(join_code: str) -> str:
    """Return the Redis key for the presence set of *join_code*."""
    return f"room:{join_code}:presence"


def _canvas_key(join_code: str) -> str:
    """Return the Redis key for the canvas snapshot of *join_code*."""
    return f"room:{join_code}:canvas"


def _presence_connections_key(join_code: str, session_key: str) -> str:
    """Return the Redis key for active socket IDs of one room/session pair."""
    return f"room:{join_code}:presence:{session_key}:connections"


# ---------------------------------------------------------------------------
# Presence API
# ---------------------------------------------------------------------------


def add_presence(
    client: "_redis.Redis",
    join_code: str,
    session_key: str,
    *,
    connection_id: str | None = None,
) -> None:
    """Mark *session_key* as connected to the room identified by *join_code*.

    Creates the presence set if it doesn't exist and resets the TTL.
    When *connection_id* is provided, it is also tracked so the session stays
    present until its final socket disconnects.
    """
    key = _presence_key(join_code)
    client.sadd(key, session_key)
    client.expire(key, ROOM_KEY_TTL)
    if connection_id is not None:
        connections_key = _presence_connections_key(join_code, session_key)
        client.sadd(connections_key, connection_id)
        client.expire(connections_key, ROOM_KEY_TTL)


def remove_presence(
    client: "_redis.Redis",
    join_code: str,
    session_key: str,
    *,
    connection_id: str | None = None,
) -> None:
    """Remove *session_key* from the room's connected-session set.

    Safe to call even if *session_key* is not in the set or the key doesn't
    exist at all. When *connection_id* is provided, the session is only
    removed from the room presence set after its final tracked socket leaves.
    """
    if connection_id is None:
        client.srem(_presence_key(join_code), session_key)
        return

    connections_key = _presence_connections_key(join_code, session_key)
    client.srem(connections_key, connection_id)
    if client.scard(connections_key) == 0:
        client.delete(connections_key)
        client.srem(_presence_key(join_code), session_key)


def get_presence(client: "_redis.Redis", join_code: str) -> set[str]:
    """Return the set of session keys currently connected to *join_code*.

    Returns an empty set if the presence key does not exist in Redis.
    The returned values are decoded to ``str`` (keys are stored as bytes
    internally; the redis-py client decodes them when ``decode_responses``
    is enabled on the client, but we handle both cases).
    """
    raw: set[bytes | str] = client.smembers(_presence_key(join_code))
    return {v.decode() if isinstance(v, bytes) else v for v in raw}


def is_present(client: "_redis.Redis", join_code: str, session_key: str) -> bool:
    """Return ``True`` if *session_key* is in the room's presence set."""
    return bool(client.sismember(_presence_key(join_code), session_key))


def clear_presence(client: "_redis.Redis", join_code: str) -> None:
    """Delete the entire presence set for *join_code*.

    Intended for use when a room closes or is deleted.
    """
    client.delete(_presence_key(join_code))
    pattern = _presence_connections_key(join_code, "*")
    connection_keys = list(client.scan_iter(match=pattern))
    if connection_keys:
        client.delete(*connection_keys)


# ---------------------------------------------------------------------------
# Canvas snapshot API
# ---------------------------------------------------------------------------


def set_canvas_snapshot(
    client: "_redis.Redis", join_code: str, data: bytes
) -> None:
    """Store *data* as the latest canvas snapshot for *join_code*.

    Overwrites any previously stored snapshot and resets the TTL.
    *data* is expected to be raw bytes (e.g. a serialised drawing command
    list or a binary canvas export).  Pass ``b""`` to initialise the key as
    a placeholder with no real content yet.
    """
    key = _canvas_key(join_code)
    client.set(key, data, ex=ROOM_KEY_TTL)


def get_canvas_snapshot(client: "_redis.Redis", join_code: str) -> bytes | None:
    """Return the stored canvas snapshot for *join_code*, or ``None``.

    Returns raw bytes when a snapshot exists.  Returns ``None`` when the key
    is absent (the room has never had a snapshot written, or the key expired).
    """
    value = client.get(_canvas_key(join_code))
    if value is None:
        return None
    # redis-py may return str when decode_responses=True; normalise to bytes.
    return value.encode() if isinstance(value, str) else value


def clear_canvas_snapshot(client: "_redis.Redis", join_code: str) -> None:
    """Delete the canvas snapshot key for *join_code*.

    Intended for use when a game ends or a room closes.
    """
    client.delete(_canvas_key(join_code))
