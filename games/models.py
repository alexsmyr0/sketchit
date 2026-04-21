from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class GameStatus(models.TextChoices):
    IN_PROGRESS = "in_progress", "In progress"
    FINISHED = "finished", "Finished"
    CANCELLED = "cancelled", "Cancelled"


class RoundStatus(models.TextChoices):
    COMPLETED = "completed", "Completed"
    DRAWER_DISCONNECTED = "drawer_disconnected", "Drawer disconnected"
    CANCELLED = "cancelled", "Cancelled"


class Game(TimestampedModel):
    room = models.ForeignKey(
        "rooms.Room",
        on_delete=models.CASCADE,
        related_name="games",
    )
    status = models.CharField(
        max_length=16,
        choices=GameStatus,
        default=GameStatus.IN_PROGRESS,
    )
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("-started_at", "-id")

    def clean(self):
        super().clean()

        if self.ended_at and self.ended_at < self.started_at:
            raise ValidationError({"ended_at": "ended_at cannot be earlier than started_at."})

        if self.status == GameStatus.IN_PROGRESS and self.ended_at is not None:
            raise ValidationError(
                {"ended_at": "An in-progress game cannot have an ended_at timestamp."}
            )

        if self.status in {GameStatus.FINISHED, GameStatus.CANCELLED} and self.ended_at is None:
            raise ValidationError(
                {"ended_at": "Finished and cancelled games must have an ended_at timestamp."}
            )

    def __str__(self):
        room_pk = self.__dict__.get("room_id")
        return f"Game #{self.pk} in room {room_pk}"


class GameWord(TimestampedModel):
    game = models.ForeignKey(
        Game,
        on_delete=models.CASCADE,
        related_name="snapshot_words",
    )
    text = models.CharField(max_length=255)

    class Meta:
        ordering = ("text", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("game", "text"),
                name="games_gameword_unique_game_text",
            )
        ]

    def __str__(self):
        return self.text


class Round(TimestampedModel):
    game = models.ForeignKey(
        Game,
        on_delete=models.CASCADE,
        related_name="rounds",
    )
    drawer_participant = models.ForeignKey(
        "rooms.Player",
        on_delete=models.SET_NULL,
        related_name="drawn_rounds",
        blank=True,
        null=True,
    )
    drawer_nickname = models.CharField(max_length=24)
    selected_game_word = models.OneToOneField(
        GameWord,
        on_delete=models.PROTECT,
        related_name="round",
    )
    sequence_number = models.PositiveIntegerField()
    # Active rounds are represented by ended_at=None and status=None.
    status = models.CharField(
        max_length=24,
        choices=RoundStatus,
        blank=True,
        null=True,
    )
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("game_id", "sequence_number")
        constraints = [
            models.UniqueConstraint(
                fields=("game", "sequence_number"),
                name="games_round_unique_game_sequence",
            )
        ]

    def clean(self):
        super().clean()

        errors = {}
        game_id = self.__dict__.get("game_id")
        selected_game_word_id = self.__dict__.get("selected_game_word_id")
        drawer_participant_id = self.__dict__.get("drawer_participant_id")

        if self.ended_at and self.ended_at < self.started_at:
            errors["ended_at"] = "ended_at cannot be earlier than started_at."

        if self.status and self.ended_at is None:
            errors["status"] = "A terminal turn status requires ended_at to be set."

        if self.ended_at and self.status is None:
            errors["status"] = "Ended rounds must have a terminal status."

        if (
            selected_game_word_id is not None
            and game_id is not None
            and self.selected_game_word.game != self.game
        ):
            errors["selected_game_word"] = "selected_game_word must belong to the same game."

        if (
            drawer_participant_id is not None
            and game_id is not None
            and self.drawer_participant.room != self.game.room
        ):
            errors["drawer_participant"] = "drawer_participant must belong to the game's room."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        game_pk = self.__dict__.get("game_id")
        return f"Round {self.sequence_number} of game {game_pk}"


class Guess(TimestampedModel):
    round = models.ForeignKey(
        Round,
        on_delete=models.CASCADE,
        related_name="guesses",
    )
    player = models.ForeignKey(
        "rooms.Player",
        on_delete=models.CASCADE,
        related_name="guesses",
    )
    text = models.CharField(max_length=255)
    normalized_text = models.TextField(default="")
    is_correct = models.BooleanField(default=False)
    typed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("round_id", "typed_at", "id")
        indexes = [
            models.Index(
                fields=("round", "player"),
                name="games_guess_round_player",
            ),
        ]

    def save(self, *args, **kwargs):
        self.normalized_text = " ".join(self.text.strip().split()).casefold()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.text
