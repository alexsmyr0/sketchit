import random
from dataclasses import dataclass

from django.db import transaction

from games.models import Game, GameStatus, GameWord, Round
from rooms.models import Player, Room


class StartGameError(Exception):
    pass


@dataclass(frozen=True)
class StartedGame:
    game: Game
    first_round: Round


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
    snapshot_word_texts = list(dict.fromkeys(room_word_texts))
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
