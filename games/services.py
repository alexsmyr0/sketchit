import random
from datetime import datetime
from dataclasses import dataclass

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from games.models import Game, GameStatus, GameWord, Guess, Round, RoundStatus
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


CORRECT_GUESSER_SCORE_DELTA = 1
DRAWER_SCORE_DELTA_ON_CORRECT_GUESS = 1


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


def _progress_game_after_round_completion(completed_round: Round) -> None:
    locked_game = Game.objects.select_for_update().get(pk=completed_round.game_id)
    if locked_game.status != GameStatus.IN_PROGRESS:
        return

    next_round_sequence_number = completed_round.sequence_number + 1
    if locked_game.rounds.filter(sequence_number=next_round_sequence_number).exists():
        return

    remaining_drawers = _get_remaining_eligible_drawers(locked_game)
    if not remaining_drawers:
        locked_game.status = GameStatus.FINISHED
        locked_game.ended_at = timezone.now()
        locked_game.save(update_fields=["status", "ended_at", "updated_at"])
        return

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
    next_word = random.choice(available_words)
    Round.objects.create(
        game=locked_game,
        drawer_participant=next_drawer,
        drawer_nickname=next_drawer.display_name,
        selected_game_word=next_word,
        sequence_number=next_round_sequence_number,
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

    locked_room.status = Room.Status.IN_PROGRESS
    locked_room.save(update_fields=["status", "updated_at"])

    return StartedGame(game=game, first_round=first_round)


@transaction.atomic
def evaluate_guess_for_round(round: Round, player: Player, guess_text: str) -> GuessEvaluationResult:
    locked_round = (
        Round.objects.select_for_update()
        .select_related("game", "selected_game_word")
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

    locked_round.status = RoundStatus.COMPLETED
    locked_round.ended_at = timezone.now()
    locked_round.save(update_fields=["status", "ended_at", "updated_at"])

    score_deltas_by_participant_id = {
        guessing_player.id: CORRECT_GUESSER_SCORE_DELTA,
    }
    drawer_participant_id = locked_round.drawer_participant_id
    if drawer_participant_id is not None and drawer_participant_id != guessing_player.id:
        score_deltas_by_participant_id[drawer_participant_id] = DRAWER_SCORE_DELTA_ON_CORRECT_GUESS

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
    _progress_game_after_round_completion(locked_round)

    return _build_guess_evaluation_result(
        guess=guess,
        locked_round=locked_round,
        is_correct=True,
        round_completed_now=True,
        winning_player_id=guessing_player.id,
        score_updates=updated_scores,
    )
