"""Management command for deleting expired empty-grace rooms."""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand
import redis

from rooms.services import cleanup_expired_empty_rooms


_redis_client = None


def _get_redis_client() -> redis.Redis:
    """Return a cached Redis client for empty-room cleanup tasks."""

    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(settings.REDIS_URL)
    return _redis_client


class Command(BaseCommand):
    help = "Delete rooms whose 10-minute empty-grace deadline has expired."

    def handle(self, *args, **options) -> None:
        """Run one cleanup sweep and report how many rooms were deleted."""

        deleted_count = cleanup_expired_empty_rooms(
            redis_client=_get_redis_client(),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted_count} expired empty room(s)."
            )
        )
