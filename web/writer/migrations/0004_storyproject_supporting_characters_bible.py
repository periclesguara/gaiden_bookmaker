from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("writer", "0003_writer_workflow")]

    operations = [
        migrations.AddField(
            model_name="storyproject",
            name="supporting_characters_bible",
            field=models.TextField(blank=True),
        ),
    ]
