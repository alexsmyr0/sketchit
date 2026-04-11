"""Shared realtime channel-group naming helpers.

These helpers are used across websocket consumers and game runtime broadcast
code so all realtime publishers/subscribers use identical group names.
"""


def room_group_name(join_code: str) -> str:
    """Return the room-wide channel group name for a join code."""
    return f"room_{join_code}"


def player_group_name(join_code: str, player_id: int) -> str:
    """Return the room-scoped per-player channel group name."""
    return f"room_{join_code}_player_{player_id}"

