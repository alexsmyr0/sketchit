from django.db import migrations, models


def populate_room_game_mode(apps, schema_editor):
    Room = apps.get_model("rooms", "Room")
    Room.objects.filter(game_mode__isnull=True).update(game_mode="normal")


class Migration(migrations.Migration):

    dependencies = [
        ("rooms", "0003_alter_player_connection_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="room",
            name="game_mode",
            field=models.CharField(
                choices=[("normal", "Normal"), ("duo", "Duo")],
                max_length=10,
                null=True,
            ),
        ),
        migrations.RunPython(
            code=populate_room_game_mode,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="room",
            name="game_mode",
            field=models.CharField(
                choices=[("normal", "Normal"), ("duo", "Duo")],
                default="normal",
                max_length=10,
            ),
        ),
    ]
