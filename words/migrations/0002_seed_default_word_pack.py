from django.db import migrations


WORD_PACK_NAME = "Default Word Pack"

WORD_PACK_WORDS = [
    "bicycle",
    "airplane",
    "guitar",
    "camera",
    "pizza",
    "robot",
    "rocket",
    "castle",
    "bridge",
    "island",
    "pirate",
    "dragon",
    "monster",
    "rainbow",
    "volcano",
    "snowman",
    "pencil",
    "backpack",
    "glasses",
    "clock",
]


def seed_word_pack(apps, schema_editor):
    word_pack_model = apps.get_model("words", "WordPack")
    word_model = apps.get_model("words", "Word")
    word_pack_entry_model = apps.get_model("words", "WordPackEntry")
    db_alias = schema_editor.connection.alias

    word_pack, _ = word_pack_model.objects.using(db_alias).get_or_create(name=WORD_PACK_NAME)
    for word_text in WORD_PACK_WORDS:
        word, _ = word_model.objects.using(db_alias).get_or_create(text=word_text)
        word_pack_entry_model.objects.using(db_alias).get_or_create(word_pack=word_pack, word=word)


def remove_word_pack(apps, schema_editor):
    word_pack_model = apps.get_model("words", "WordPack")
    word_model = apps.get_model("words", "Word")
    word_pack_entry_model = apps.get_model("words", "WordPackEntry")
    db_alias = schema_editor.connection.alias

    try:
        word_pack = word_pack_model.objects.using(db_alias).get(name=WORD_PACK_NAME)
    except word_pack_model.DoesNotExist:
        return

    entry_qs = word_pack_entry_model.objects.using(db_alias).filter(
        word_pack=word_pack,
        word__text__in=WORD_PACK_WORDS,
    )
    seeded_word_ids = list(entry_qs.values_list("word_id", flat=True))
    entry_qs.delete()

    for word_id in seeded_word_ids:
        if not word_pack_entry_model.objects.using(db_alias).filter(word_id=word_id).exists():
            word_model.objects.using(db_alias).filter(id=word_id).delete()

    if not word_pack_entry_model.objects.using(db_alias).filter(word_pack=word_pack).exists():
        word_pack.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("words", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_word_pack, remove_word_pack),
    ]
