from django.contrib import admin
from django.test import SimpleTestCase

from words.admin import WordAdmin, WordPackAdmin, WordPackEntryAdmin
from words.models import Word, WordPack, WordPackEntry


class WordsAdminRegistrationTests(SimpleTestCase):
    def test_words_domain_models_are_registered_in_admin(self):
        self.assertIsInstance(admin.site._registry.get(WordPack), WordPackAdmin)
        self.assertIsInstance(admin.site._registry.get(Word), WordAdmin)
        self.assertIsInstance(admin.site._registry.get(WordPackEntry), WordPackEntryAdmin)

    def test_word_pack_admin_configuration_matches_expected_setup(self):
        word_pack_admin = admin.site._registry[WordPack]

        self.assertEqual(word_pack_admin.list_display, ("id", "name", "created_at", "updated_at"))
        self.assertEqual(word_pack_admin.search_fields, ("name",))
        self.assertEqual(word_pack_admin.readonly_fields, ("created_at", "updated_at"))

    def test_word_admin_configuration_matches_expected_setup(self):
        word_admin = admin.site._registry[Word]

        self.assertEqual(word_admin.list_display, ("id", "text", "created_at", "updated_at"))
        self.assertEqual(word_admin.search_fields, ("text",))
        self.assertEqual(word_admin.readonly_fields, ("created_at", "updated_at"))

    def test_word_pack_entry_admin_configuration_matches_expected_setup(self):
        word_pack_entry_admin = admin.site._registry[WordPackEntry]

        self.assertEqual(word_pack_entry_admin.list_display, ("id", "word_pack", "word", "created_at", "updated_at"))
        self.assertEqual(word_pack_entry_admin.list_filter, ("created_at",))
        self.assertEqual(word_pack_entry_admin.search_fields, ("word_pack__name", "word__text"))
        self.assertEqual(word_pack_entry_admin.raw_id_fields, ("word_pack", "word"))
        self.assertEqual(word_pack_entry_admin.readonly_fields, ("created_at", "updated_at"))
        self.assertEqual(word_pack_entry_admin.list_select_related, ("word_pack", "word"))
