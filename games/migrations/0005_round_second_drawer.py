from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("rooms", "0004_room_game_mode"),
        ("games", "0004_game_game_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="round",
            name="second_drawer_nickname",
            field=models.CharField(blank=True, max_length=24, null=True),
        ),
        migrations.AddField(
            model_name="round",
            name="second_drawer_participant",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="second_drawn_rounds",
                to="rooms.player",
            ),
        ),
    ]
