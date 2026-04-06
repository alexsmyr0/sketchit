from django.contrib import admin

from .models import Game, GameWord, Guess, Round


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "room",
        "status",
        "started_at",
        "ended_at",
        "created_at",
        "updated_at",
    )
    list_filter = ("status", "started_at", "created_at")
    search_fields = ("room__join_code", "room__name")
    ordering = ("-started_at", "-id")
    raw_id_fields = ("room",)
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("room",)


@admin.register(GameWord)
class GameWordAdmin(admin.ModelAdmin):
    list_display = ("id", "text", "game", "created_at", "updated_at")
    list_filter = ("created_at",)
    search_fields = ("text", "game__room__join_code", "game__room__name")
    ordering = ("text", "id")
    raw_id_fields = ("game",)
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("game", "game__room")


@admin.register(Round)
class RoundAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "game",
        "sequence_number",
        "drawer_nickname",
        "drawer_participant",
        "status",
        "started_at",
        "ended_at",
        "created_at",
        "updated_at",
    )
    list_filter = ("status", "started_at", "created_at")
    search_fields = (
        "drawer_nickname",
        "game__room__join_code",
        "game__room__name",
        "selected_game_word__text",
    )
    ordering = ("game_id", "sequence_number")
    raw_id_fields = ("game", "drawer_participant", "selected_game_word")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("game", "game__room", "drawer_participant", "selected_game_word")


@admin.register(Guess)
class GuessAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "round",
        "player",
        "text",
        "typed_at",
        "created_at",
        "updated_at",
    )
    list_filter = ("typed_at", "created_at")
    search_fields = (
        "text",
        "player__display_name",
        "player__room__join_code",
        "round__game__room__join_code",
    )
    ordering = ("round_id", "typed_at", "id")
    raw_id_fields = ("round", "player")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("round", "round__game", "player", "player__room")
