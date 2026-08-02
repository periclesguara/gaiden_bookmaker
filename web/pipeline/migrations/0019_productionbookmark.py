from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0017_editionpipeline_last_version"),
        ("pipeline", "0018_manualtranslationjob"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductionBookmark",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(default="current", editable=False, max_length=32, unique=True)),
                ("target_language", models.CharField(blank=True, default="", max_length=16)),
                ("saved_at", models.DateTimeField(auto_now=True)),
                (
                    "edition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="production_bookmarks",
                        to="editorial.edition",
                    ),
                ),
            ],
            options={"ordering": ["-saved_at"]},
        ),
    ]
