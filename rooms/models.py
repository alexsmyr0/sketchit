from django.db import models
from django.utils import timezone


class PlayerQuerySet(models.QuerySet):
    def expired(self):
        return self.filter(session_expires_at__lte=timezone.now())


class PlayerManager(models.Manager):
    def get_queryset(self):
        return PlayerQuerySet(self.model, using=self._db)

    def expired(self):
        return self.get_queryset().expired()

    def purge_expired(self):
        deleted_count, _ = self.expired().delete()
        return deleted_count


MVP_DEFAULT_WORD_PACK_NAME = "Default Word Pack"


def get_mvp_default_word_pack_id():
    from words.models import WordPack

    default_word_pack = (
        WordPack.objects.filter(
            name=MVP_DEFAULT_WORD_PACK_NAME,
            word_pack_entries__isnull=False,
        )
        .order_by("id")
        .first()
    )
    if default_word_pack is None:
        raise RuntimeError(
            "Cannot create a room without a valid default word pack. "
            f"Expected '{MVP_DEFAULT_WORD_PACK_NAME}' with at least one word."
        )

    return default_word_pack.id


class Room(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public"
        PRIVATE = "private", "Private"

    class Status(models.TextChoices):
        LOBBY = "lobby", "Lobby"
        IN_PROGRESS = "in_progress", "In progress"
        EMPTY_GRACE = "empty_grace", "Empty grace"

    name = models.CharField(max_length=255)
    join_code = models.CharField(max_length=8, unique=True)
    visibility = models.CharField(
        max_length=10,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.LOBBY,
        db_index=True,
    )
    max_players = models.PositiveSmallIntegerField(default=6)
    settings = models.JSONField(default=dict, blank=True)
    word_pack = models.ForeignKey(
        "words.WordPack",
        on_delete=models.PROTECT,
        related_name="rooms",
        default=get_mvp_default_word_pack_id,
    )
    empty_since = models.DateTimeField(null=True, blank=True)
    host = models.ForeignKey(
        "Player",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hosted_rooms",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.join_code


class Player(models.Model):
    class ConnectionStatus(models.TextChoices):
        CONNECTED = "connected", "Connected"
        DISCONNECTED = "disconnected", "Disconnected"

    class ParticipationStatus(models.TextChoices):
        PLAYING = "playing", "Playing"
        SPECTATING = "spectating", "Spectating"

    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    session_key = models.CharField(max_length=64)
    display_name = models.CharField(max_length=24)
    connection_status = models.CharField(
        max_length=20,
        choices=ConnectionStatus.choices,
        default=ConnectionStatus.CONNECTED,
    )
    participation_status = models.CharField(
        max_length=20,
        choices=ParticipationStatus.choices,
        default=ParticipationStatus.PLAYING,
    )
    current_score = models.IntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    session_expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PlayerManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["room", "session_key"],
                name="uq_players_room_session",
            )
        ]

    def __str__(self) -> str:
        return self.display_name

    @property
    def is_session_expired(self) -> bool:
        return self.session_expires_at <= timezone.now()
