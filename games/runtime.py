"""Server-owned round timer and intermission runtime coordinator.

This module currently uses in-process threads and module-level state for timer
handles and the Redis client cache. That means timers are process-local: this
implementation is suitable for single-process runtime environments and tests.

For multi-process deployments, timer ownership must move to a distributed
coordinator/worker model with shared locking so only one worker owns each room
timer at a time.
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

import redis
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from core.realtime_groups import player_group_name, room_group_name
from django.conf import settings
from django.utils import timezone
from redis.exceptions import WatchError

from games import redis as game_redis
from games.models import Round
from rooms.models import Player


_redis_client = None
_room_timer_lock = threading.Lock()


@dataclass
class _RoomTimerHandles:
    active_round_id: int | None = None
    round_stop_event: threading.Event | None = None
    round_thread: threading.Thread | None = None
    intermission_stop_event: threading.Event | None = None
    intermission_thread: threading.Thread | None = None


_room_timer_handles_by_join_code: dict[str, _RoomTimerHandles] = {}


def _runtime_enabled() -> bool:
    return bool(getattr(settings, "SKETCHIT_ENABLE_RUNTIME_COORDINATOR", True))


def _round_duration_seconds() -> float:
    return float(getattr(settings, "SKETCHIT_ROUND_DURATION_SECONDS", 90))


def _intermission_duration_seconds() -> float:
    return float(getattr(settings, "SKETCHIT_INTERMISSION_DURATION_SECONDS", 10))


def _timer_tick_interval_seconds() -> float:
    return float(getattr(settings, "SKETCHIT_TIMER_TICK_INTERVAL_SECONDS", 1))


def get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(settings.REDIS_URL)
    return _redis_client


def _decode_json_int_list(raw_value: str | None) -> set[int]:
    if not raw_value:
        return set()
    try:
        decoded = json.loads(raw_value)
    except json.JSONDecodeError:
        return set()
    return {int(item) for item in decoded}


def _decode_hash_to_str_dict(raw_state: dict) -> dict[str, str]:
    return {
        (key.decode() if isinstance(key, bytes) else key): (
            value.decode() if isinstance(value, bytes) else value
        )
        for key, value in raw_state.items()
    }


def _parse_int(raw_value: str | None) -> int | None:
    if raw_value in (None, ""):
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _parse_iso_datetime(raw_value: str | None) -> datetime | None:
    if not raw_value:
        return None
    try:
        return datetime.fromisoformat(raw_value)
    except ValueError:
        return None


def _remaining_seconds(deadline_at: datetime) -> int:
    return max(0, int(math.ceil((deadline_at - timezone.now()).total_seconds())))


def _stop_thread(
    *,
    stop_event: threading.Event | None,
    thread: threading.Thread | None,
) -> None:
    if stop_event is not None:
        stop_event.set()
    if (
        thread is not None
        and thread.is_alive()
        and thread is not threading.current_thread()
    ):
        thread.join(timeout=0.3)


def _get_or_create_room_timer_handles(join_code: str) -> _RoomTimerHandles:
    handles = _room_timer_handles_by_join_code.get(join_code)
    if handles is None:
        handles = _RoomTimerHandles()
        _room_timer_handles_by_join_code[join_code] = handles
    return handles


def _cancel_round_timer(join_code: str) -> None:
    with _room_timer_lock:
        handles = _room_timer_handles_by_join_code.get(join_code)
        if handles is None:
            return
        stop_event = handles.round_stop_event
        thread = handles.round_thread
        handles.round_stop_event = None
        handles.round_thread = None
        handles.active_round_id = None

    _stop_thread(stop_event=stop_event, thread=thread)


def _cancel_intermission_timer(join_code: str) -> None:
    with _room_timer_lock:
        handles = _room_timer_handles_by_join_code.get(join_code)
        if handles is None:
            return
        stop_event = handles.intermission_stop_event
        thread = handles.intermission_thread
        handles.intermission_stop_event = None
        handles.intermission_thread = None

    _stop_thread(
        stop_event=stop_event,
        thread=thread,
    )


def reset_runtime_state_for_tests() -> None:
    join_codes = []
    with _room_timer_lock:
        join_codes.extend(_room_timer_handles_by_join_code.keys())

    for join_code in join_codes:
        _cancel_round_timer(join_code)
        _cancel_intermission_timer(join_code)

    with _room_timer_lock:
        _room_timer_handles_by_join_code.clear()

    global _redis_client
    _redis_client = None


def _clear_guess_state_keys_for_room(client: redis.Redis, join_code: str) -> None:
    """Delete every per-round guess-state hash for one room.

    Guess-state keys include the round id in the key name, so a room-wide
    teardown must scan for them rather than clearing only one known round.
    """

    pattern = f"room:{join_code}:round:*:guess_state"
    keys = list(client.scan_iter(match=pattern))
    if keys:
        client.delete(*keys)


def teardown_room_runtime(
    join_code: str,
    *,
    redis_client: redis.Redis | None = None,
    include_cleanup_deadline: bool = False,
) -> None:
    """Stop local timer workers and clear Redis runtime state for one room.

    This is used when a room is abandoned or deleted so stale timer threads do
    not keep broadcasting for gameplay that is no longer valid.
    """

    _cancel_round_timer(join_code)
    _cancel_intermission_timer(join_code)

    client = redis_client or get_redis_client()
    _clear_guess_state_keys_for_room(client, join_code)
    game_redis.clear_turn_state(client, join_code)
    game_redis.clear_drawer_pool(client, join_code)
    game_redis.clear_round_payloads(client, join_code)
    game_redis.clear_deadline(client, join_code, "round_end")
    game_redis.clear_deadline(client, join_code, "intermission_end")
    if include_cleanup_deadline:
        game_redis.clear_deadline(client, join_code, "cleanup")


def get_timer_status_for_tests(join_code: str) -> dict[str, bool]:
    with _room_timer_lock:
        handles = _room_timer_handles_by_join_code.get(join_code)
        if handles is None:
            return {
                "round_timer_running": False,
                "intermission_timer_running": False,
            }

        return {
            "round_timer_running": bool(
                handles.round_thread is not None and handles.round_thread.is_alive()
            ),
            "intermission_timer_running": bool(
                handles.intermission_thread is not None
                and handles.intermission_thread.is_alive()
            ),
        }


def broadcast_room_event(join_code: str, event_type: str, payload: dict) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    async_to_sync(channel_layer.group_send)(
        room_group_name(join_code),
        {
            "type": "room.server_event",
            "event": {
                "type": event_type,
                "payload": payload,
            },
        },
    )


def broadcast_player_event(
    join_code: str,
    player_id: int,
    event_type: str,
    payload: dict,
) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    async_to_sync(channel_layer.group_send)(
        player_group_name(join_code, player_id),
        {
            "type": "room.server_event",
            "event": {
                "type": event_type,
                "payload": payload,
            },
        },
    )


def _next_timer_tick(
    *,
    join_code: str,
    expected_phase: str,
    expected_round_id: int,
    sequence_field: str,
) -> tuple[int, str] | None:
    client = get_redis_client()
    turn_state = game_redis.get_turn_state(client, join_code)
    if not turn_state:
        return None
    if turn_state.get("phase") != expected_phase:
        return None
    if turn_state.get("round_id") != str(expected_round_id):
        return None

    sequence = (_parse_int(turn_state.get(sequence_field)) or 0) + 1
    server_timestamp = timezone.now().isoformat()
    game_redis.update_turn_state_fields(
        client,
        join_code,
        {
            sequence_field: str(sequence),
            "last_timer_server_timestamp": server_timestamp,
        },
    )
    return sequence, server_timestamp


def _round_timer_worker(
    *,
    join_code: str,
    round_id: int,
    deadline_at: datetime,
    stop_event: threading.Event,
) -> None:
    tick_interval_seconds = max(_timer_tick_interval_seconds(), 0.05)

    while not stop_event.is_set():
        remaining = _remaining_seconds(deadline_at)
        tick_data = _next_timer_tick(
            join_code=join_code,
            expected_phase="round",
            expected_round_id=round_id,
            sequence_field="round_timer_sequence",
        )
        if tick_data is None:
            return
        tick_sequence, server_timestamp = tick_data

        broadcast_room_event(
            join_code,
            "round.timer",
            {
                "round_id": round_id,
                "phase": "round",
                "deadline_at": deadline_at.isoformat(),
                "remaining_seconds": remaining,
                "tick_sequence": tick_sequence,
                "server_timestamp": server_timestamp,
            },
        )
        if remaining <= 0:
            break

        seconds_until_deadline = max(
            0.0, (deadline_at - timezone.now()).total_seconds()
        )
        wait_seconds = min(tick_interval_seconds, seconds_until_deadline)
        if wait_seconds <= 0:
            continue
        if stop_event.wait(wait_seconds):
            return

    if stop_event.is_set():
        return

    from games.services import complete_round_due_to_timer

    complete_round_due_to_timer(round_id)


def _intermission_timer_worker(
    *,
    join_code: str,
    completed_round_id: int,
    deadline_at: datetime,
    stop_event: threading.Event,
) -> None:
    tick_interval_seconds = max(_timer_tick_interval_seconds(), 0.05)

    while not stop_event.is_set():
        remaining = _remaining_seconds(deadline_at)
        tick_data = _next_timer_tick(
            join_code=join_code,
            expected_phase="intermission",
            expected_round_id=completed_round_id,
            sequence_field="intermission_timer_sequence",
        )
        if tick_data is None:
            return
        tick_sequence, server_timestamp = tick_data

        broadcast_room_event(
            join_code,
            "round.intermission_timer",
            {
                "completed_round_id": completed_round_id,
                "phase": "intermission",
                "deadline_at": deadline_at.isoformat(),
                "remaining_seconds": remaining,
                "tick_sequence": tick_sequence,
                "server_timestamp": server_timestamp,
            },
        )
        if remaining <= 0:
            break

        seconds_until_deadline = max(
            0.0, (deadline_at - timezone.now()).total_seconds()
        )
        wait_seconds = min(tick_interval_seconds, seconds_until_deadline)
        if wait_seconds <= 0:
            continue
        if stop_event.wait(wait_seconds):
            return

    if stop_event.is_set():
        return

    from games.services import advance_game_after_intermission

    result = advance_game_after_intermission(completed_round_id)
    if result.next_round_id is not None:
        start_round_runtime(result.next_round_id)
        return

    if result.game_finished:
        client = get_redis_client()
        game_redis.clear_turn_state(client, join_code)
        game_redis.clear_round_payloads(client, join_code)
        game_redis.clear_drawer_pool(client, join_code)
        game_redis.clear_deadline(client, join_code, "round_end")
        game_redis.clear_deadline(client, join_code, "intermission_end")
        broadcast_room_event(
            join_code,
            "game.finished",
            {
                "game_id": result.game_id,
                "status": "finished",
                "server_timestamp": timezone.now().isoformat(),
            },
        )


def _start_round_timer(
    *,
    join_code: str,
    round_id: int,
    deadline_at: datetime,
) -> None:
    with _room_timer_lock:
        handles = _get_or_create_room_timer_handles(join_code)
        old_stop_event = handles.round_stop_event
        old_thread = handles.round_thread
        handles.round_stop_event = None
        handles.round_thread = None
        handles.active_round_id = None

    _stop_thread(stop_event=old_stop_event, thread=old_thread)

    stop_event = threading.Event()
    thread = threading.Thread(
        target=_round_timer_worker,
        kwargs={
            "join_code": join_code,
            "round_id": round_id,
            "deadline_at": deadline_at,
            "stop_event": stop_event,
        },
        daemon=True,
        name=f"round-timer-{join_code}-{round_id}",
    )

    with _room_timer_lock:
        handles = _get_or_create_room_timer_handles(join_code)
        handles.round_stop_event = stop_event
        handles.round_thread = thread
        handles.active_round_id = round_id

    thread.start()


def _start_intermission_timer(
    *,
    join_code: str,
    completed_round_id: int,
    deadline_at: datetime,
) -> None:
    with _room_timer_lock:
        handles = _get_or_create_room_timer_handles(join_code)
        old_stop_event = handles.intermission_stop_event
        old_thread = handles.intermission_thread
        handles.intermission_stop_event = None
        handles.intermission_thread = None

    _stop_thread(stop_event=old_stop_event, thread=old_thread)

    stop_event = threading.Event()
    thread = threading.Thread(
        target=_intermission_timer_worker,
        kwargs={
            "join_code": join_code,
            "completed_round_id": completed_round_id,
            "deadline_at": deadline_at,
            "stop_event": stop_event,
        },
        daemon=True,
        name=f"intermission-timer-{join_code}-{completed_round_id}",
    )

    with _room_timer_lock:
        handles = _get_or_create_room_timer_handles(join_code)
        handles.intermission_stop_event = stop_event
        handles.intermission_thread = thread

    thread.start()


def _eligible_guesser_ids_for_round(round: Round) -> list[int]:
    return list(
        Player.objects.filter(
            room_id=round.game.room_id,
            participation_status=Player.ParticipationStatus.PLAYING,
            created_at__lte=round.started_at,
        )
        .exclude(pk=round.drawer_participant_id)
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )


def _mask_word_for_guessers(word_text: str) -> str:
    return "".join("_" if character.isalnum() else character for character in word_text)


def _build_round_start_payloads(
    *,
    round: Round,
    deadline_at: datetime,
) -> tuple[dict, dict]:
    server_timestamp = timezone.now().isoformat()
    masked_word = _mask_word_for_guessers(round.selected_game_word.text)
    common_payload = {
        "game_id": round.game_id,
        "round_id": round.id,
        "sequence_number": round.sequence_number,
        "drawer_participant_id": round.drawer_participant_id,
        "deadline_at": deadline_at.isoformat(),
        "duration_seconds": _round_duration_seconds(),
        "masked_word": masked_word,
        "server_timestamp": server_timestamp,
    }
    drawer_payload = {
        **common_payload,
        "role": "drawer",
        "word": round.selected_game_word.text,
    }
    guesser_payload = {
        **common_payload,
        "role": "guesser",
    }
    return drawer_payload, guesser_payload


def get_round_correctness_state(
    *,
    join_code: str,
    round_id: int,
) -> tuple[set[int], set[int]] | None:
    if not _runtime_enabled():
        return None

    client = get_redis_client()
    turn_state = game_redis.get_turn_state(client, join_code)
    if not turn_state:
        return None
    if turn_state.get("round_id") != str(round_id):
        return None

    return (
        _decode_json_int_list(turn_state.get("eligible_guesser_ids")),
        _decode_json_int_list(turn_state.get("correct_guesser_ids")),
    )


def _build_round_state_payload(turn_state: dict[str, str]) -> dict | None:
    phase = turn_state.get("phase")
    if phase not in {"round", "intermission"}:
        return None

    deadline_at_raw = turn_state.get("deadline_at")
    deadline_at = _parse_iso_datetime(deadline_at_raw)
    if deadline_at is None:
        return None

    sequence_field = (
        "round_timer_sequence"
        if phase == "round"
        else "intermission_timer_sequence"
    )

    payload = {
        "phase": phase,
        "status": turn_state.get("status", phase),
        "game_id": _parse_int(turn_state.get("game_id")),
        "round_id": _parse_int(turn_state.get("round_id")),
        "drawer_participant_id": _parse_int(turn_state.get("drawer_participant_id")),
        "deadline_at": deadline_at.isoformat(),
        "remaining_seconds": _remaining_seconds(deadline_at),
        "tick_sequence": _parse_int(turn_state.get(sequence_field)) or 0,
        "server_timestamp": timezone.now().isoformat(),
    }

    completed_round_sequence = _parse_int(turn_state.get("completed_round_sequence"))
    if completed_round_sequence is not None:
        payload["completed_round_sequence"] = completed_round_sequence

    ended_at_raw = turn_state.get("ended_at")
    if ended_at_raw:
        payload["ended_at"] = ended_at_raw

    return payload


def get_sync_events_for_player(join_code: str, player_id: int) -> list[dict]:
    if not _runtime_enabled():
        return []

    client = get_redis_client()
    turn_state = game_redis.get_turn_state(client, join_code)
    if not turn_state:
        return []

    round_state_payload = _build_round_state_payload(turn_state)
    if round_state_payload is None:
        return []

    events: list[dict] = [
        {
            "type": "round.state",
            "payload": round_state_payload,
        }
    ]

    if round_state_payload["phase"] == "round":
        events.append(
            {
                "type": "round.timer",
                "payload": {
                    "round_id": round_state_payload["round_id"],
                    "phase": "round",
                    "deadline_at": round_state_payload["deadline_at"],
                    "remaining_seconds": round_state_payload["remaining_seconds"],
                    "tick_sequence": round_state_payload["tick_sequence"],
                    "server_timestamp": round_state_payload["server_timestamp"],
                },
            }
        )
    else:
        events.append(
            {
                "type": "round.intermission_timer",
                "payload": {
                    "completed_round_id": round_state_payload["round_id"],
                    "phase": "intermission",
                    "deadline_at": round_state_payload["deadline_at"],
                    "remaining_seconds": round_state_payload["remaining_seconds"],
                    "tick_sequence": round_state_payload["tick_sequence"],
                    "server_timestamp": round_state_payload["server_timestamp"],
                },
            }
        )

    drawer_id = round_state_payload.get("drawer_participant_id")
    round_id = round_state_payload.get("round_id")
    if round_state_payload["phase"] == "round":
        role = "drawer" if drawer_id is not None and drawer_id == player_id else "guesser"
        role_payload = game_redis.get_round_payload(client, join_code, role)
        if role_payload is not None:
            events.append(
                {
                    "type": "round.started",
                    "payload": role_payload,
                }
            )
            if role == "drawer":
                events.append(
                    {
                        "type": "round.drawer_word",
                        "payload": {
                            "round_id": role_payload.get("round_id", round_id),
                            "word": role_payload.get("word"),
                            "server_timestamp": timezone.now().isoformat(),
                        },
                    }
                )
            return events

    if (
        round_state_payload["phase"] == "round"
        and drawer_id is not None
        and round_id is not None
        and drawer_id == player_id
    ):
        round = (
            Round.objects.select_related("selected_game_word")
            .filter(pk=round_id)
            .first()
        )
        if round is not None:
            events.append(
                {
                    "type": "round.drawer_word",
                    "payload": {
                        "round_id": round.id,
                        "word": round.selected_game_word.text,
                        "server_timestamp": timezone.now().isoformat(),
                    },
                }
            )

    return events


def start_round_runtime(round_id: int) -> None:
    if not _runtime_enabled():
        return

    round = (
        Round.objects.select_related("game__room", "selected_game_word")
        .filter(pk=round_id)
        .first()
    )
    if round is None or round.status is not None or round.ended_at is not None:
        return

    join_code = round.game.room.join_code
    eligible_guesser_ids = _eligible_guesser_ids_for_round(round)
    started_at = timezone.now()
    now_iso = started_at.isoformat()
    deadline_at = started_at + timedelta(seconds=_round_duration_seconds())
    drawer_payload, guesser_payload = _build_round_start_payloads(
        round=round,
        deadline_at=deadline_at,
    )

    client = get_redis_client()
    game_redis.clear_guess_state(client, join_code, round.id)
    game_redis.set_turn_state(
        client,
        join_code,
        {
            "phase": "round",
            "status": "drawing",
            "game_id": str(round.game_id),
            "round_id": str(round.id),
            "drawer_participant_id": (
                str(round.drawer_participant_id)
                if round.drawer_participant_id is not None
                else ""
            ),
            "started_at": now_iso,
            "deadline_at": deadline_at.isoformat(),
            "eligible_guesser_ids": json.dumps(sorted(eligible_guesser_ids)),
            "correct_guesser_ids": json.dumps([]),
            "round_timer_sequence": "0",
            "intermission_timer_sequence": "0",
            "last_timer_server_timestamp": now_iso,
        },
    )
    game_redis.set_round_payloads(
        client,
        join_code,
        drawer_payload=drawer_payload,
        guesser_payload=guesser_payload,
    )
    game_redis.set_deadline(client, join_code, "round_end", deadline_at.isoformat())
    game_redis.clear_deadline(client, join_code, "intermission_end")

    _cancel_intermission_timer(join_code)
    _start_round_timer(join_code=join_code, round_id=round.id, deadline_at=deadline_at)

    broadcast_room_event(
        join_code,
        "round.started",
        guesser_payload,
    )

    if round.drawer_participant_id is not None:
        # Keep role-specific reveal as a separate channelled event so guessers
        # never receive the full answer while allowing K-04 to build on this
        # start-round hook.
        broadcast_player_event(
            join_code,
            round.drawer_participant_id,
            "round.drawer_word",
            {
                "round_id": drawer_payload["round_id"],
                "word": drawer_payload["word"],
                "server_timestamp": drawer_payload["server_timestamp"],
            },
        )


def mark_guesser_correct(*, join_code: str, round_id: int, player_id: int) -> bool:
    if not _runtime_enabled():
        return False

    client = get_redis_client()
    turn_state_key = game_redis.get_turn_state_key(join_code)
    max_retries = 8

    for _ in range(max_retries):
        with client.pipeline() as pipeline:
            try:
                pipeline.watch(turn_state_key)
                raw_turn_state = pipeline.hgetall(turn_state_key)
                turn_state = _decode_hash_to_str_dict(raw_turn_state)
                if not turn_state:
                    pipeline.unwatch()
                    return False
                if turn_state.get("phase") != "round":
                    pipeline.unwatch()
                    return False
                if turn_state.get("round_id") != str(round_id):
                    pipeline.unwatch()
                    return False

                eligible_guesser_ids = _decode_json_int_list(
                    turn_state.get("eligible_guesser_ids")
                )
                if player_id not in eligible_guesser_ids:
                    pipeline.unwatch()
                    return False

                correct_guesser_ids = _decode_json_int_list(
                    turn_state.get("correct_guesser_ids")
                )
                if player_id in correct_guesser_ids:
                    pipeline.unwatch()
                    return bool(eligible_guesser_ids) and eligible_guesser_ids.issubset(
                        correct_guesser_ids
                    )

                correct_guesser_ids.add(player_id)
                pipeline.multi()
                pipeline.hset(
                    turn_state_key,
                    mapping={
                        "correct_guesser_ids": json.dumps(
                            sorted(correct_guesser_ids)
                        ),
                    },
                )
                pipeline.expire(turn_state_key, game_redis.ROOM_RUNTIME_TTL)
                pipeline.execute()

                game_redis.set_guess_state(
                    client,
                    join_code,
                    round_id,
                    player_id,
                    {
                        "status": "correct",
                        "recorded_at": timezone.now().isoformat(),
                    },
                )
                return bool(eligible_guesser_ids) and eligible_guesser_ids.issubset(
                    correct_guesser_ids
                )
            except WatchError:
                continue

    # If contention is high we fail safe without force-writing guesser state.
    return False


def start_intermission(
    *,
    join_code: str,
    completed_round_id: int,
    completed_round_sequence: int,
    ended_at_iso: str,
    completion_reason: str,
) -> None:
    if not _runtime_enabled():
        return

    _cancel_round_timer(join_code)
    deadline_at = timezone.now() + timedelta(seconds=_intermission_duration_seconds())
    now_iso = timezone.now().isoformat()

    client = get_redis_client()
    current_turn_state = game_redis.get_turn_state(client, join_code)
    eligible_guesser_ids = current_turn_state.get("eligible_guesser_ids", "[]")
    correct_guesser_ids = current_turn_state.get("correct_guesser_ids", "[]")
    game_id = current_turn_state.get("game_id", "")
    drawer_participant_id = current_turn_state.get("drawer_participant_id", "")
    round_timer_sequence = current_turn_state.get("round_timer_sequence", "0")

    game_redis.set_turn_state(
        client,
        join_code,
        {
            "phase": "intermission",
            "status": "intermission",
            "game_id": game_id,
            "round_id": str(completed_round_id),
            "drawer_participant_id": drawer_participant_id,
            "completed_round_sequence": str(completed_round_sequence),
            "ended_at": ended_at_iso,
            "deadline_at": deadline_at.isoformat(),
            "eligible_guesser_ids": eligible_guesser_ids,
            "correct_guesser_ids": correct_guesser_ids,
            "round_timer_sequence": round_timer_sequence,
            "intermission_timer_sequence": "0",
            "last_timer_server_timestamp": now_iso,
        },
    )
    game_redis.clear_round_payloads(client, join_code)
    game_redis.clear_deadline(client, join_code, "round_end")
    game_redis.set_deadline(client, join_code, "intermission_end", deadline_at.isoformat())

    broadcast_room_event(
        join_code,
        "round.ended",
        {
            "round_id": completed_round_id,
            "status": "completed",
            "reason": completion_reason,
            "ended_at": ended_at_iso,
            "server_timestamp": timezone.now().isoformat(),
        },
    )
    broadcast_room_event(
        join_code,
        "round.intermission_started",
        {
            "completed_round_id": completed_round_id,
            "completed_round_sequence": completed_round_sequence,
            "deadline_at": deadline_at.isoformat(),
            "duration_seconds": _intermission_duration_seconds(),
            "tick_sequence": 0,
            "server_timestamp": timezone.now().isoformat(),
        },
    )

    _start_intermission_timer(
        join_code=join_code,
        completed_round_id=completed_round_id,
        deadline_at=deadline_at,
    )
