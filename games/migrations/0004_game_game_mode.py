from django.db import migrations, models


def populate_game_game_mode(apps, schema_editor):
    Game = apps.get_model("games", "Game")
    for game in Game.objects.select_related("room").all().iterator():
        Game.objects.filter(pk=game.pk).update(
            game_mode=getattr(game.room, "game_mode", "normal") or "normal"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("rooms", "0004_room_game_mode"),
        ("games", "0003_guess_normalized_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="game",
            name="game_mode",
            field=models.CharField(
                choices=[("normal", "Normal"), ("duo", "Duo")],
                max_length=10,
                null=True,
            ),
        ),
        migrations.RunPython(
            code=populate_game_game_mode,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="game",
            name="game_mode",
            field=models.CharField(
                choices=[("normal", "Normal"), ("duo", "Duo")],
                default="normal",
                max_length=10,
            ),
        ),
    ]
