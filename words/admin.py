from django.contrib import admin

from .models import Word, WordPack, WordPackEntry


@admin.register(WordPack)
class WordPackAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "created_at", "updated_at")
    search_fields = ("name",)
    ordering = ("name", "id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Word)
class WordAdmin(admin.ModelAdmin):
    list_display = ("id", "text", "created_at", "updated_at")
    search_fields = ("text",)
    ordering = ("text", "id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(WordPackEntry)
class WordPackEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "word_pack", "word", "created_at", "updated_at")
    list_filter = ("created_at",)
    search_fields = ("word_pack__name", "word__text")
    ordering = ("word_pack_id", "word_id", "id")
    raw_id_fields = ("word_pack", "word")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("word_pack", "word")
