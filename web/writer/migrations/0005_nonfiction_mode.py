from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("writer", "0004_supporting_cast_revisions"),
    ]

    operations = [
        migrations.AddField(
            model_name="storyproject",
            name="writing_mode",
            field=models.CharField(
                choices=[
                    ("FICTION", "Fiction — Ficção"),
                    ("NONFICTION", "Nonfiction — Não ficção"),
                ],
                default="FICTION",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="chapter",
            name="source_guidance",
            field=models.TextField(blank=True),
        ),
    ]
