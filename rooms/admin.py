from django.contrib import admin

from .models import Player, Room


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "join_code",
        "name",
        "visibility",
        "status",
        "max_players",
        "host",
        "empty_since",
        "created_at",
        "updated_at",
    )
    list_filter = ("visibility", "status", "created_at", "updated_at")
    search_fields = ("join_code", "name", "host__display_name")
    ordering = ("-created_at", "-id")
    raw_id_fields = ("host",)
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("host",)


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "display_name",
        "room",
        "connection_status",
        "participation_status",
        "current_score",
        "session_expires_at",
        "last_seen_at",
        "created_at",
        "updated_at",
    )
    list_filter = ("connection_status", "participation_status", "created_at")
    search_fields = ("display_name", "session_key", "room__join_code", "room__name")
    ordering = ("-created_at", "-id")
    raw_id_fields = ("room",)
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("room",)
