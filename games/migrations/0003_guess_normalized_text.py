from django.db import migrations, models


def _normalize_guess_text(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def populate_normalized_guess_text(apps, schema_editor):
    Guess = apps.get_model("games", "Guess")
    for guess in Guess.objects.all().only("id", "text").iterator():
        Guess.objects.filter(pk=guess.pk).update(
            normalized_text=_normalize_guess_text(guess.text or "")
        )


def clear_normalized_guess_text(apps, schema_editor):
    Guess = apps.get_model("games", "Guess")
    Guess.objects.update(normalized_text="")


class Migration(migrations.Migration):

    dependencies = [
        ("games", "0002_guess_is_correct"),
    ]

    operations = [
        migrations.AddField(
            model_name="guess",
            name="normalized_text",
            field=models.CharField(db_index=True, default="", max_length=255),
        ),
        migrations.RunPython(
            code=populate_normalized_guess_text,
            reverse_code=clear_normalized_guess_text,
        ),
        migrations.AddIndex(
            model_name="guess",
            index=models.Index(
                fields=["round", "player", "normalized_text"],
                name="games_guess_round_player_norm",
            ),
        ),
    ]
