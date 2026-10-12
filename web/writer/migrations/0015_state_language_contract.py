from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("writer", "0014_project_languages_and_work_links")]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="storyproject",
                    name="language_contract",
                    field=models.JSONField(blank=True, default=dict),
                ),
            ],
            database_operations=[],
        ),
    ]
