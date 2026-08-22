from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("writer", "0003_storyproject_supporting_characters_bible"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupportingCastRevision",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "version",
                    models.PositiveIntegerField(
                        validators=[MinValueValidator(1)]
                    ),
                ),
                ("instruction", models.TextField()),
                ("registry", models.JSONField(default=dict)),
                ("registry_sha256", models.CharField(max_length=64)),
                (
                    "source_chunk_ids",
                    models.JSONField(blank=True, default=list),
                ),
                ("source_scores", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="writer_supporting_cast_revisions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="supporting_cast_revisions",
                        to="writer.storyproject",
                    ),
                ),
            ],
            options={"ordering": ("project_id", "-version")},
        ),
        migrations.AddConstraint(
            model_name="supportingcastrevision",
            constraint=models.UniqueConstraint(
                fields=("project", "version"),
                name="writer_unique_supporting_cast_revision",
            ),
        ),
        migrations.AddField(
            model_name="chaptersession",
            name="supporting_cast_revision",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sessions",
                to="writer.supportingcastrevision",
            ),
        ),
        migrations.AddField(
            model_name="chaptersession",
            name="supporting_cast_sha256",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="chaptersession",
            name="supporting_cast_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
