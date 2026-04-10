from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from datetime import timedelta

import redis
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.utils import timezone

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


def _remaining_seconds(deadline_at) -> int:
    return max(0, int(math.ceil((deadline_at - timezone.now()).total_seconds())))


def _room_group_name(join_code: str) -> str:
    return f"room_{join_code}"


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
        _stop_thread(stop_event=handles.round_stop_event, thread=handles.round_thread)
        handles.round_stop_event = None
        handles.round_thread = None
        handles.active_round_id = None


def _cancel_intermission_timer(join_code: str) -> None:
    with _room_timer_lock:
        handles = _room_timer_handles_by_join_code.get(join_code)
        if handles is None:
            return
        _stop_thread(
            stop_event=handles.intermission_stop_event,
            thread=handles.intermission_thread,
        )
        handles.intermission_stop_event = None
        handles.intermission_thread = None


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


def broadcast_room_event(join_code: str, event_type: str, payload: dict) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    async_to_sync(channel_layer.group_send)(
        _room_group_name(join_code),
        {
            "type": "room.server_event",
            "event": {
                "type": event_type,
                "payload": payload,
            },
        },
    )


def _round_timer_worker(
    *,
    join_code: str,
    round_id: int,
    deadline_at,
    stop_event: threading.Event,
) -> None:
    tick_interval_seconds = max(_timer_tick_interval_seconds(), 0.05)

    while not stop_event.is_set():
        remaining = _remaining_seconds(deadline_at)
        broadcast_room_event(
            join_code,
            "round.timer",
            {
                "round_id": round_id,
                "phase": "round",
                "deadline_at": deadline_at.isoformat(),
                "remaining_seconds": remaining,
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
    deadline_at,
    stop_event: threading.Event,
) -> None:
    tick_interval_seconds = max(_timer_tick_interval_seconds(), 0.05)

    while not stop_event.is_set():
        remaining = _remaining_seconds(deadline_at)
        broadcast_room_event(
            join_code,
            "round.intermission_timer",
            {
                "completed_round_id": completed_round_id,
                "phase": "intermission",
                "deadline_at": deadline_at.isoformat(),
                "remaining_seconds": remaining,
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
        game_redis.clear_deadline(client, join_code, "round_end")
        game_redis.clear_deadline(client, join_code, "intermission_end")
        broadcast_room_event(
            join_code,
            "game.finished",
            {
                "game_id": result.game_id,
                "status": "finished",
            },
        )


def _start_round_timer(
    *,
    join_code: str,
    round_id: int,
    deadline_at,
) -> None:
    with _room_timer_lock:
        handles = _get_or_create_room_timer_handles(join_code)
        _stop_thread(stop_event=handles.round_stop_event, thread=handles.round_thread)

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
        handles.round_stop_event = stop_event
        handles.round_thread = thread
        handles.active_round_id = round_id
        thread.start()


def _start_intermission_timer(
    *,
    join_code: str,
    completed_round_id: int,
    deadline_at,
) -> None:
    with _room_timer_lock:
        handles = _get_or_create_room_timer_handles(join_code)
        _stop_thread(
            stop_event=handles.intermission_stop_event,
            thread=handles.intermission_thread,
        )

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


def start_round_runtime(round_id: int) -> None:
    if not _runtime_enabled():
        return

    round = (
        Round.objects.select_related("game__room")
        .filter(pk=round_id)
        .first()
    )
    if round is None or round.status is not None or round.ended_at is not None:
        return

    join_code = round.game.room.join_code
    eligible_guesser_ids = _eligible_guesser_ids_for_round(round)
    deadline_at = timezone.now() + timedelta(seconds=_round_duration_seconds())

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
            "started_at": timezone.now().isoformat(),
            "deadline_at": deadline_at.isoformat(),
            "eligible_guesser_ids": json.dumps(sorted(eligible_guesser_ids)),
            "correct_guesser_ids": json.dumps([]),
        },
    )
    game_redis.set_deadline(client, join_code, "round_end", deadline_at.isoformat())
    game_redis.clear_deadline(client, join_code, "intermission_end")

    _cancel_intermission_timer(join_code)
    _start_round_timer(join_code=join_code, round_id=round.id, deadline_at=deadline_at)

    broadcast_room_event(
        join_code,
        "round.started",
        {
            "game_id": round.game_id,
            "round_id": round.id,
            "sequence_number": round.sequence_number,
            "drawer_participant_id": round.drawer_participant_id,
            "deadline_at": deadline_at.isoformat(),
            "duration_seconds": _round_duration_seconds(),
        },
    )
    broadcast_room_event(
        join_code,
        "round.timer",
        {
            "round_id": round.id,
            "phase": "round",
            "deadline_at": deadline_at.isoformat(),
            "remaining_seconds": _remaining_seconds(deadline_at),
        },
    )


def mark_guesser_correct(*, join_code: str, round_id: int, player_id: int) -> bool:
    if not _runtime_enabled():
        return False

    client = get_redis_client()
    turn_state = game_redis.get_turn_state(client, join_code)
    if not turn_state:
        return False
    if turn_state.get("phase") != "round":
        return False
    if turn_state.get("round_id") != str(round_id):
        return False

    eligible_guesser_ids = _decode_json_int_list(turn_state.get("eligible_guesser_ids"))
    if player_id not in eligible_guesser_ids:
        return False

    correct_guesser_ids = _decode_json_int_list(turn_state.get("correct_guesser_ids"))
    if player_id not in correct_guesser_ids:
        correct_guesser_ids.add(player_id)
        turn_state["correct_guesser_ids"] = json.dumps(sorted(correct_guesser_ids))
        game_redis.set_turn_state(client, join_code, turn_state)
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

    return bool(eligible_guesser_ids) and eligible_guesser_ids.issubset(correct_guesser_ids)


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

    client = get_redis_client()
    current_turn_state = game_redis.get_turn_state(client, join_code)
    eligible_guesser_ids = current_turn_state.get("eligible_guesser_ids", "[]")
    correct_guesser_ids = current_turn_state.get("correct_guesser_ids", "[]")
    game_id = current_turn_state.get("game_id", "")

    game_redis.set_turn_state(
        client,
        join_code,
        {
            "phase": "intermission",
            "status": "intermission",
            "game_id": game_id,
            "round_id": str(completed_round_id),
            "completed_round_sequence": str(completed_round_sequence),
            "ended_at": ended_at_iso,
            "deadline_at": deadline_at.isoformat(),
            "eligible_guesser_ids": eligible_guesser_ids,
            "correct_guesser_ids": correct_guesser_ids,
        },
    )
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
        },
    )

    _start_intermission_timer(
        join_code=join_code,
        completed_round_id=completed_round_id,
        deadline_at=deadline_at,
    )

