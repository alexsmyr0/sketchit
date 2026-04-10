from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("rooms", "0002_room_word_pack"),
    ]

    operations = [
        migrations.AlterField(
            model_name="player",
            name="connection_status",
            field=models.CharField(
                choices=[("connected", "Connected"), ("disconnected", "Disconnected")],
                default="disconnected",
                max_length=20,
            ),
        ),
    ]
