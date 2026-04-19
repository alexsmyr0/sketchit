import random
from dataclasses import dataclass
from datetime import datetime, timedelta

import redis
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from redis.exceptions import RedisError

from games import redis as game_redis
from games.models import Game, GameStatus, GameWord, Guess, Round, RoundStatus
from rooms import redis as room_redis
from rooms.models import Player, Room


class StartGameError(Exception):
    pass


class GuessEvaluationError(Exception):
    pass


@dataclass(frozen=True)
class StartedGame:
    game: Game
    first_round: Round


@dataclass(frozen=True)
class PlayerScoreUpdate:
    player_id: int
    current_score: int


@dataclass(frozen=True)
class GuessEvaluationResult:
    guess: Guess
    is_correct: bool
    round_completed: bool
    round_completed_now: bool
    round_status: str | None
    round_ended_at: datetime | None
    winning_player_id: int | None
    score_updates: tuple[PlayerScoreUpdate, ...]

    def as_round_result(self) -> dict[str, object]:
        return {
            "round_id": self.guess.round_id,
            "status": self.round_status,
            "ended_at": self.round_ended_at,
            "winning_player_id": self.winning_player_id,
            "score_updates": [
                {
                    "player_id": score_update.player_id,
                    "current_score": score_update.current_score,
                }
                for score_update in self.score_updates
            ],
        }


@dataclass(frozen=True)
class IntermissionAdvanceResult:
    join_code: str
    game_id: int
    game_finished: bool
    next_round_id: int | None


MIN_GUESSER_SCORE = 20
MAX_GUESSER_SCORE = 100
MIN_DRAWER_BONUS = 10
MAX_DRAWER_BONUS = 50


_redis_client: redis.Redis | None = None


def _runtime_coordinator_enabled() -> bool:
    return bool(getattr(settings, "SKETCHIT_ENABLE_RUNTIME_COORDINATOR", True))


def _get_redis_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(settings.REDIS_URL)
    return _redis_client


def _set_remaining_drawer_pool(*, join_code: str, participant_ids: list[int]) -> None:
    try:
        game_redis.set_drawer_pool(_get_redis_client(), join_code, participant_ids)
    except RedisError:
        # Durable game progression remains DB-authoritative if Redis is unavailable.
        return


def _clear_round_runtime_payloads(join_code: str) -> None:
    try:
        game_redis.clear_round_payloads(_get_redis_client(), join_code)
    except RedisError:
        return


def _dedupe_snapshot_words_case_insensitive(word_texts: list[str]) -> list[str]:
    unique_words_by_normalized_text: dict[str, str] = {}
    for word_text in word_texts:
        normalized_text = word_text.casefold()
        if normalized_text in unique_words_by_normalized_text:
            continue
        unique_words_by_normalized_text[normalized_text] = word_text

    return list(unique_words_by_normalized_text.values())


def _normalize_guess_text(value: str) -> str:
    return value.strip().casefold()


def _round_duration_seconds() -> float:
    """Return validated round duration seconds from settings."""
    try:
        configured_duration = float(
            getattr(settings, "SKETCHIT_ROUND_DURATION_SECONDS", 90)
        )
    except (TypeError, ValueError):
        configured_duration = 90.0
    return max(configured_duration, 0.001)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse an aware ISO timestamp string and return None on invalid input."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if timezone.is_naive(parsed):
        return None
    return parsed


def _runtime_round_deadline_for_scoring(locked_round: Round) -> datetime | None:
    """Read live round deadline from runtime turn-state when available."""
    if not _runtime_coordinator_enabled():
        return None

    try:
        from games import runtime as game_runtime

        turn_state = game_redis.get_turn_state(
            game_runtime.get_redis_client(),
            locked_round.game.room.join_code,
        )
    except (RedisError, OSError):
        return None

    if turn_state.get("phase") != "round":
        return None
    if turn_state.get("round_id") != str(locked_round.id):
        return None
    return _parse_iso_datetime(turn_state.get("deadline_at"))


def _bounded_linear_score(*, minimum: int, maximum: int, ratio: float) -> int:
    """Map a normalized ratio to a rounded linear score within configured bounds."""
    bounded_ratio = min(1.0, max(0.0, ratio))
    unbounded_score = minimum + bounded_ratio * (maximum - minimum)
    return int(round(unbounded_score))


def _time_based_scores_for_correct_guess(
    *,
    locked_round: Round,
    accepted_at: datetime,
) -> tuple[int, int]:
    """Compute guesser and drawer scores based on accepted guess time."""
    round_duration_seconds = _round_duration_seconds()
    round_duration_ms = max(1.0, round_duration_seconds * 1000.0)

    runtime_deadline = _runtime_round_deadline_for_scoring(locked_round)
    deadline_at = runtime_deadline or (
        locked_round.started_at + timedelta(seconds=round_duration_seconds)
    )
    remaining_ms = max(
        0.0,
        (deadline_at - accepted_at).total_seconds() * 1000.0,
    )
    remaining_ratio = min(1.0, max(0.0, remaining_ms / round_duration_ms))

    guesser_points = _bounded_linear_score(
        minimum=MIN_GUESSER_SCORE,
        maximum=MAX_GUESSER_SCORE,
        ratio=remaining_ratio,
    )
    drawer_bonus = _bounded_linear_score(
        minimum=MIN_DRAWER_BONUS,
        maximum=MAX_DRAWER_BONUS,
        ratio=remaining_ratio,
    )
    return guesser_points, drawer_bonus


def _get_round_winning_player_id(round_id: int) -> int | None:
    return (
        Guess.objects.filter(round_id=round_id, is_correct=True)
        .order_by("typed_at", "id")
        .values_list("player_id", flat=True)
        .first()
    )


def _build_guess_evaluation_result(
    *,
    guess: Guess,
    locked_round: Round,
    is_correct: bool,
    round_completed_now: bool,
    winning_player_id: int | None,
    score_updates: tuple[PlayerScoreUpdate, ...],
) -> GuessEvaluationResult:
    round_completed = bool(locked_round.status or locked_round.ended_at)
    return GuessEvaluationResult(
        guess=guess,
        is_correct=is_correct,
        round_completed=round_completed,
        round_completed_now=round_completed_now,
        round_status=locked_round.status,
        round_ended_at=locked_round.ended_at,
        winning_player_id=winning_player_id,
        score_updates=score_updates,
    )


def _get_eligible_drawers_for_game(game: Game) -> list[Player]:
    return list(
        Player.objects.select_for_update()
        .filter(
            room_id=game.room_id,
            participation_status=Player.ParticipationStatus.PLAYING,
            connection_status=Player.ConnectionStatus.CONNECTED,
        )
        .order_by("created_at", "id")
    )


def _get_remaining_eligible_drawers(game: Game) -> list[Player]:
    eligible_drawers = _get_eligible_drawers_for_game(game)
    already_drawn_participant_ids = set(
        game.rounds.exclude(drawer_participant_id__isnull=True).values_list(
            "drawer_participant_id",
            flat=True,
        )
    )
    return [
        participant
        for participant in eligible_drawers
        if participant.id not in already_drawn_participant_ids
    ]


def _get_round_eligible_guesser_ids(round: Round) -> list[int]:
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


def _all_eligible_non_drawer_guessers_are_correct(
    *,
    locked_round: Round,
    newest_correct_guesser_id: int,
) -> bool:
    if _runtime_coordinator_enabled():
        from games import runtime as game_runtime

        runtime_says_all_correct = game_runtime.mark_guesser_correct(
            join_code=locked_round.game.room.join_code,
            round_id=locked_round.id,
            player_id=newest_correct_guesser_id,
        )
        if runtime_says_all_correct:
            return True

        runtime_correctness_state = game_runtime.get_round_correctness_state(
            join_code=locked_round.game.room.join_code,
            round_id=locked_round.id,
        )
        if runtime_correctness_state is not None:
            eligible_guesser_ids, correct_guesser_ids = runtime_correctness_state
            return bool(eligible_guesser_ids) and eligible_guesser_ids.issubset(
                correct_guesser_ids
            )

    eligible_guesser_ids = set(_get_round_eligible_guesser_ids(locked_round))
    if not eligible_guesser_ids:
        return False

    correct_guesser_ids = set(
        Guess.objects.filter(
            round_id=locked_round.id,
            is_correct=True,
            player_id__in=eligible_guesser_ids,
        ).values_list("player_id", flat=True)
    )
    return eligible_guesser_ids.issubset(correct_guesser_ids)


def _schedule_round_runtime_start(round_id: int) -> None:
    from games import runtime as game_runtime

    game_runtime.start_round_runtime(round_id)


def _schedule_round_intermission_start(
    *,
    join_code: str,
    completed_round_id: int,
    completed_round_sequence: int,
    ended_at_iso: str,
    completion_reason: str,
) -> None:
    from games import runtime as game_runtime

    game_runtime.start_intermission(
        join_code=join_code,
        completed_round_id=completed_round_id,
        completed_round_sequence=completed_round_sequence,
        ended_at_iso=ended_at_iso,
        completion_reason=completion_reason,
    )


def _handle_round_completed(locked_round: Round, *, completion_reason: str) -> None:
    # Clear round-specific Redis canvas state when it completes
    client = _get_redis_client()
    room_redis.clear_canvas_snapshot(client, locked_round.game.room.join_code)

    if _runtime_coordinator_enabled():
        transaction.on_commit(
            lambda: _schedule_round_intermission_start(
                join_code=locked_round.game.room.join_code,
                completed_round_id=locked_round.id,
                completed_round_sequence=locked_round.sequence_number,
                ended_at_iso=locked_round.ended_at.isoformat(),
                completion_reason=completion_reason,
            )
        )
        return

    _progress_game_after_round_completion(locked_round)


def _progress_game_after_round_completion(completed_round: Round) -> Round | None:
    locked_game = Game.objects.select_for_update().get(pk=completed_round.game_id)
    join_code = locked_game.room.join_code
    next_round_sequence_number = completed_round.sequence_number + 1

    existing_next_round = (
        locked_game.rounds.select_related("selected_game_word", "game__room")
        .filter(sequence_number=next_round_sequence_number)
        .first()
    )
    if existing_next_round is not None:
        return existing_next_round

    if locked_game.status != GameStatus.IN_PROGRESS:
        return None

    # A-07: spectators who joined mid-game must be promoted to PLAYING before
    # we compute the next drawer pool. Promoting here — inside the same atomic
    # transaction that picks the next drawer — guarantees the newly eligible
    # players are always visible to _get_remaining_eligible_drawers without a
    # separate read-after-write race. The lazy import avoids a circular
    # dependency: rooms.services already imports from games at module level.
    from rooms.services import (
        promote_mid_game_spectators_to_players,
        schedule_room_state_broadcast_after_commit,
    )
    promoted_count = promote_mid_game_spectators_to_players(
        room_id=locked_game.room_id,
    )
    if promoted_count > 0:
        # A-06 is the authoritative source for lobby rendering. When
        # participation_status flips for one or more players we must re-emit a
        # room.state snapshot so connected clients stop showing the promoted
        # participant as a spectator. Scheduling on commit ties the broadcast
        # to the round-transition transaction so it only fires if the
        # promotion actually persists.
        schedule_room_state_broadcast_after_commit(
            join_code=join_code,
            room_id=locked_game.room_id,
        )

    remaining_drawers = _get_remaining_eligible_drawers(locked_game)
    remaining_drawer_ids = [participant.id for participant in remaining_drawers]
    if not remaining_drawers:
        _set_remaining_drawer_pool(join_code=join_code, participant_ids=[])
        locked_game.status = GameStatus.FINISHED
        locked_game.ended_at = timezone.now()
        locked_game.save(update_fields=["status", "ended_at", "updated_at"])
        _clear_round_runtime_payloads(join_code)
        return None

    available_words = list(
        locked_game.snapshot_words.select_for_update()
        .filter(round__isnull=True)
        .order_by("id")
    )
    if not available_words:
        raise GuessEvaluationError(
            "Cannot continue game because no unused snapshot words remain."
        )

    next_drawer = random.choice(remaining_drawers)
    _set_remaining_drawer_pool(
        join_code=join_code,
        participant_ids=[
            participant_id
            for participant_id in remaining_drawer_ids
            if participant_id != next_drawer.id
        ],
    )
    next_word = random.choice(available_words)
    return Round.objects.create(
        game=locked_game,
        drawer_participant=next_drawer,
        drawer_nickname=next_drawer.display_name,
        selected_game_word=next_word,
        sequence_number=next_round_sequence_number,
    )


@transaction.atomic
def advance_game_after_intermission(completed_round_id: int) -> IntermissionAdvanceResult:
    completed_round = (
        Round.objects.select_for_update()
        .select_related("game__room")
        .get(pk=completed_round_id)
    )
    next_round = _progress_game_after_round_completion(completed_round)
    completed_round.game.refresh_from_db(fields=("status",))

    return IntermissionAdvanceResult(
        join_code=completed_round.game.room.join_code,
        game_id=completed_round.game_id,
        game_finished=completed_round.game.status == GameStatus.FINISHED,
        next_round_id=next_round.id if next_round is not None else None,
    )


@transaction.atomic
def start_game_for_room(room: Room) -> StartedGame:
    locked_room = Room.objects.select_for_update().select_related("word_pack").get(pk=room.pk)
    if locked_room.status != Room.Status.LOBBY:
        raise StartGameError("A game can only be started while the room is in lobby status.")

    eligible_participants = list(
        Player.objects.select_for_update()
        .filter(
            room=locked_room,
            participation_status=Player.ParticipationStatus.PLAYING,
            connection_status=Player.ConnectionStatus.CONNECTED,
        )
        .order_by("created_at", "id")
    )
    if len(eligible_participants) < 2:
        raise StartGameError("At least 2 eligible participants are required to start a game.")

    room_word_texts = list(
        locked_room.word_pack.word_pack_entries.order_by("id").values_list("word__text", flat=True)
    )
    snapshot_word_texts = _dedupe_snapshot_words_case_insensitive(room_word_texts)
    if not snapshot_word_texts:
        raise StartGameError("The room's selected word list has no words.")

    # A new game starts with fresh totals for every participant currently in the room.
    locked_room.participants.update(current_score=0)

    game = Game.objects.create(
        room=locked_room,
        status=GameStatus.IN_PROGRESS,
    )
    GameWord.objects.bulk_create(
        [GameWord(game=game, text=word_text) for word_text in snapshot_word_texts]
    )
    snapshot_words = list(game.snapshot_words.order_by("id"))

    first_drawer = random.choice(eligible_participants)
    first_word = random.choice(snapshot_words)
    first_round = Round.objects.create(
        game=game,
        drawer_participant=first_drawer,
        drawer_nickname=first_drawer.display_name,
        selected_game_word=first_word,
        sequence_number=1,
    )
    _set_remaining_drawer_pool(
        join_code=locked_room.join_code,
        participant_ids=[
            participant.id
            for participant in eligible_participants
            if participant.id != first_drawer.id
        ],
    )
    _clear_round_runtime_payloads(locked_room.join_code)

    locked_room.status = Room.Status.IN_PROGRESS
    locked_room.save(update_fields=["status", "updated_at"])

    # Ensure a clean canvas when the game starts
    client = _get_redis_client()
    room_redis.clear_canvas_snapshot(client, locked_room.join_code)

    if _runtime_coordinator_enabled():
        transaction.on_commit(lambda: _schedule_round_runtime_start(first_round.id))

    return StartedGame(game=game, first_round=first_round)


@transaction.atomic
def complete_round_due_to_timer(round_id: int) -> bool:
    locked_round = (
        Round.objects.select_for_update()
        .select_related("game__room")
        .get(pk=round_id)
    )
    if locked_round.status is not None or locked_round.ended_at is not None:
        return False

    locked_round.status = RoundStatus.COMPLETED
    locked_round.ended_at = timezone.now()
    locked_round.save(update_fields=["status", "ended_at", "updated_at"])
    _handle_round_completed(locked_round, completion_reason="timer_expired")
    return True


@transaction.atomic
def evaluate_guess_for_round(round: Round, player: Player, guess_text: str) -> GuessEvaluationResult:
    locked_round = (
        Round.objects.select_for_update()
        .select_related("game__room", "selected_game_word")
        .get(pk=round.pk)
    )
    guessing_player = (
        Player.objects.select_for_update()
        .filter(
            pk=player.pk,
            room_id=locked_round.game.room_id,
        )
        .first()
    )
    if guessing_player is None:
        raise GuessEvaluationError(
            "The guessing participant must belong to the round's room."
        )
    if (
        guessing_player.connection_status != Player.ConnectionStatus.CONNECTED
        or guessing_player.participation_status != Player.ParticipationStatus.PLAYING
    ):
        raise GuessEvaluationError(
            "The guessing participant must be connected and have playing status."
        )

    guess = Guess.objects.create(
        round=locked_round,
        player=guessing_player,
        text=guess_text,
    )

    # Completed rounds are immutable; additional guesses are stored but must not
    # change scoring or winner outcome.
    if locked_round.status is not None or locked_round.ended_at is not None:
        return _build_guess_evaluation_result(
            guess=guess,
            locked_round=locked_round,
            is_correct=False,
            round_completed_now=False,
            winning_player_id=_get_round_winning_player_id(locked_round.id),
            score_updates=(),
        )

    if locked_round.drawer_participant_id == guessing_player.id:
        return _build_guess_evaluation_result(
            guess=guess,
            locked_round=locked_round,
            is_correct=False,
            round_completed_now=False,
            winning_player_id=None,
            score_updates=(),
        )

    if Guess.objects.filter(
        round_id=locked_round.id,
        player_id=guessing_player.id,
        is_correct=True,
    ).exists():
        return _build_guess_evaluation_result(
            guess=guess,
            locked_round=locked_round,
            is_correct=False,
            round_completed_now=False,
            winning_player_id=None,
            score_updates=(),
        )

    if _normalize_guess_text(guess.text) != _normalize_guess_text(locked_round.selected_game_word.text):
        return _build_guess_evaluation_result(
            guess=guess,
            locked_round=locked_round,
            is_correct=False,
            round_completed_now=False,
            winning_player_id=None,
            score_updates=(),
        )

    guess.is_correct = True
    guess.save(update_fields=["is_correct", "updated_at"])

    accepted_at = timezone.now()
    guesser_points, drawer_bonus = _time_based_scores_for_correct_guess(
        locked_round=locked_round,
        accepted_at=accepted_at,
    )
    score_deltas_by_participant_id = {
        guessing_player.id: guesser_points,
    }
    drawer_participant_id = locked_round.drawer_participant_id
    if drawer_participant_id is not None and drawer_participant_id != guessing_player.id:
        score_deltas_by_participant_id[drawer_participant_id] = drawer_bonus

    for participant_id, score_delta in score_deltas_by_participant_id.items():
        Player.objects.filter(pk=participant_id).update(
            current_score=F("current_score") + score_delta
        )

    updated_scores = tuple(
        PlayerScoreUpdate(player_id=row["id"], current_score=row["current_score"])
        for row in Player.objects.filter(pk__in=score_deltas_by_participant_id)
        .order_by("id")
        .values("id", "current_score")
    )
    all_eligible_guessers_correct = _all_eligible_non_drawer_guessers_are_correct(
        locked_round=locked_round,
        newest_correct_guesser_id=guessing_player.id,
    )
    round_completed_now = False
    if all_eligible_guessers_correct:
        locked_round.status = RoundStatus.COMPLETED
        locked_round.ended_at = accepted_at
        locked_round.save(update_fields=["status", "ended_at", "updated_at"])
        round_completed_now = True
        _handle_round_completed(locked_round, completion_reason="all_guessers_correct")

    return _build_guess_evaluation_result(
        guess=guess,
        locked_round=locked_round,
        is_correct=True,
        round_completed_now=round_completed_now,
        winning_player_id=(
            _get_round_winning_player_id(locked_round.id)
            if round_completed_now
            else None
        ),
        score_updates=updated_scores,
    )
