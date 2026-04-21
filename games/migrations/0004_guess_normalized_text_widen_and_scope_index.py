from django.db import migrations, models


def _normalize_guess_text(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def repopulate_normalized_guess_text(apps, schema_editor):
    Guess = apps.get_model("games", "Guess")
    for guess in Guess.objects.all().only("id", "text").iterator():
        Guess.objects.filter(pk=guess.pk).update(
            normalized_text=_normalize_guess_text(guess.text or "")
        )


class Migration(migrations.Migration):

    dependencies = [
        ("games", "0003_guess_normalized_text"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="guess",
            name="games_guess_round_player_norm",
        ),
        migrations.AlterField(
            model_name="guess",
            name="normalized_text",
            field=models.TextField(default=""),
        ),
        migrations.RunPython(
            code=repopulate_normalized_guess_text,
            reverse_code=repopulate_normalized_guess_text,
        ),
        migrations.AddIndex(
            model_name="guess",
            index=models.Index(
                fields=["round", "player"],
                name="games_guess_round_player",
            ),
        ),
    ]
