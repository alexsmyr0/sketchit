"""
Redis-side helpers for game runtime state.

This module owns all Redis key definitions and operations for the games app.
Like rooms/redis.py, it is dependency-free: callers pass in a redis.Redis client 
so the functions can be used with any client instance and are trivially testable with fakeredis.

Key layout
----------
room:{join_code}:game:drawer_pool
    Redis Set    Remaining drawer player IDs for the current game.
room:{join_code}:round:turn_state
    Redis Hash   Active turn state for timers (e.g., ends_at, status).
room:{join_code}:round:{round_id}:guess_state
    Redis Hash   Live JSON guess state per-round mapped by player_id.
room:{join_code}:round:payload:{role}
    Redis String JSON encoded payload for specific roles ("drawer" or "guesser").
room:{join_code}:deadline:{deadline_type}
    Redis String ISO timestamp for specific deadline types.

All keys carry a 24-hour TTL that is refreshed on every write.
"""

from __future__ import annotations
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis as _redis

# ---------------------------------------------------------------------------
# TTL
# ---------------------------------------------------------------------------

ROOM_RUNTIME_TTL = 60 * 60 * 24  # 24 hours in seconds

# ---------------------------------------------------------------------------
# Internal key builders
# ---------------------------------------------------------------------------

def _drawer_pool_key(join_code: str) -> str:
    """Return the Redis key for the drawer pool set of *join_code*."""
    return f"room:{join_code}:game:drawer_pool"


def _turn_state_key(join_code: str) -> str:
    """Return the Redis key for the active turn state hash of *join_code*."""
    return f"room:{join_code}:round:turn_state"


def _guess_state_key(join_code: str, round_id: int) -> str:
    """Return the Redis key for the active guess state hash of *join_code* and *round_id*."""
    return f"room:{join_code}:round:{round_id}:guess_state"


def _round_payload_key(join_code: str, role: str) -> str:
    """Return the Redis key for the round payload of *join_code* and *role*."""
    return f"room:{join_code}:round:payload:{role}"


def _deadline_key(join_code: str, deadline_type: str) -> str:
    """Return the Redis key for a specific *deadline_type* in *join_code*."""
    return f"room:{join_code}:deadline:{deadline_type}"

# ---------------------------------------------------------------------------
# Drawer Pool API
# ---------------------------------------------------------------------------

def set_drawer_pool(client: "_redis.Redis", join_code: str, player_ids: list[int]) -> None:
    key = _drawer_pool_key(join_code)
    client.delete(key)
    if player_ids:
        client.sadd(key, *player_ids)
        client.expire(key, ROOM_RUNTIME_TTL)

def remove_from_drawer_pool(client: "_redis.Redis", join_code: str, player_id: int) -> None:
    client.srem(_drawer_pool_key(join_code), player_id)

def get_drawer_pool(client: "_redis.Redis", join_code: str) -> set[int]:
    raw = client.smembers(_drawer_pool_key(join_code))
    return {int(v.decode() if isinstance(v, bytes) else v) for v in raw}

def clear_drawer_pool(client: "_redis.Redis", join_code: str) -> None:
    client.delete(_drawer_pool_key(join_code))

# ---------------------------------------------------------------------------
# Turn State API
# ---------------------------------------------------------------------------

def set_turn_state(client: "_redis.Redis", join_code: str, state_dict: dict[str, str | int]) -> None:
    key = _turn_state_key(join_code)
    client.delete(key)
    if state_dict:
        # Convert all to strings/bytes for consistent hash storage
        client.hset(key, mapping=state_dict)
        client.expire(key, ROOM_RUNTIME_TTL)

def update_turn_state_fields(
    client: "_redis.Redis",
    join_code: str,
    state_fields: dict[str, str | int],
) -> None:
    """Update specific fields on the room turn-state hash.

    Unlike ``set_turn_state`` this preserves untouched fields and is safer for
    concurrent runtime writers that only need to mutate one or two values.
    """
    if not state_fields:
        return

    key = _turn_state_key(join_code)
    client.hset(key, mapping=state_fields)
    client.expire(key, ROOM_RUNTIME_TTL)

def get_turn_state(client: "_redis.Redis", join_code: str) -> dict[str, str]:
    raw = client.hgetall(_turn_state_key(join_code))
    return {k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v for k, v in raw.items()}

def clear_turn_state(client: "_redis.Redis", join_code: str) -> None:
    client.delete(_turn_state_key(join_code))

# ---------------------------------------------------------------------------
# Guess State API
# ---------------------------------------------------------------------------

def set_guess_state(client: "_redis.Redis", join_code: str, round_id: int, player_id: int, state: dict) -> None:
    key = _guess_state_key(join_code, round_id)
    client.hset(key, str(player_id), json.dumps(state))
    client.expire(key, ROOM_RUNTIME_TTL)

def get_guess_state(client: "_redis.Redis", join_code: str, round_id: int, player_id: int) -> dict | None:
    val = client.hget(_guess_state_key(join_code, round_id), str(player_id))
    if val is None:
        return None
    val_str = val.decode() if isinstance(val, bytes) else val
    return json.loads(val_str)

def get_all_guess_states(client: "_redis.Redis", join_code: str, round_id: int) -> dict[int, dict]:
    raw = client.hgetall(_guess_state_key(join_code, round_id))
    return {
        int(k.decode() if isinstance(k, bytes) else k): json.loads(v.decode() if isinstance(v, bytes) else v)
        for k, v in raw.items()
    }

def clear_guess_state(client: "_redis.Redis", join_code: str, round_id: int) -> None:
    client.delete(_guess_state_key(join_code, round_id))

# ---------------------------------------------------------------------------
# Round Payload API
# ---------------------------------------------------------------------------

def set_round_payloads(client: "_redis.Redis", join_code: str, drawer_payload: dict, guesser_payload: dict) -> None:
    drawer_key = _round_payload_key(join_code, "drawer")
    guesser_key = _round_payload_key(join_code, "guesser")
    
    drawer_data = json.dumps(drawer_payload)
    guesser_data = json.dumps(guesser_payload)
    
    client.set(drawer_key, drawer_data, ex=ROOM_RUNTIME_TTL)
    client.set(guesser_key, guesser_data, ex=ROOM_RUNTIME_TTL)

def get_round_payload(client: "_redis.Redis", join_code: str, role: str) -> dict | None:
    raw = client.get(_round_payload_key(join_code, role))
    if raw:
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)
    return None

def clear_round_payloads(client: "_redis.Redis", join_code: str) -> None:
    client.delete(_round_payload_key(join_code, "drawer"))
    client.delete(_round_payload_key(join_code, "guesser"))

# ---------------------------------------------------------------------------
# Deadline API
# ---------------------------------------------------------------------------

def set_deadline(client: "_redis.Redis", join_code: str, deadline_type: str, deadline_isotimestamp: str) -> None:
    key = _deadline_key(join_code, deadline_type)
    client.set(key, deadline_isotimestamp, ex=ROOM_RUNTIME_TTL)

def get_deadline(client: "_redis.Redis", join_code: str, deadline_type: str) -> str | None:
    raw = client.get(_deadline_key(join_code, deadline_type))
    if raw:
        return raw.decode() if isinstance(raw, bytes) else raw
    return None

def clear_deadline(client: "_redis.Redis", join_code: str, deadline_type: str) -> None:
    client.delete(_deadline_key(join_code, deadline_type))
