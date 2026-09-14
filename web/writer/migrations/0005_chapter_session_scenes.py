from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("writer", "0004_storyproject_supporting_characters_bible")]
    operations = [
        migrations.AddField(
            model_name="chapter",
            name="session_scenes",
            field=models.JSONField(default=list, blank=True),
        ),
    ]
