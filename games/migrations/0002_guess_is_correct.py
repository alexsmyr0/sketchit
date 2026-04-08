from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("games", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="guess",
            name="is_correct",
            field=models.BooleanField(default=False),
        ),
    ]
