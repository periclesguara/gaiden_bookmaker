from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("writer", "0010_storyproject_drive_folder"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ManuscriptRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.PositiveIntegerField()),
                ("source_kind", models.CharField(choices=[("IMPORTED_FINAL", "Versão final importada"), ("CHAPTER_MERGE", "Merge dos capítulos")], default="IMPORTED_FINAL", max_length=32)),
                ("original_filename", models.CharField(blank=True, max_length=255)),
                ("storage_path", models.TextField()),
                ("sha256", models.CharField(max_length=64)),
                ("word_count", models.PositiveIntegerField(default=0)),
                ("character_count", models.PositiveIntegerField(default=0)),
                ("detected_chapter_count", models.PositiveIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="writer_manuscript_revisions", to=settings.AUTH_USER_MODEL)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="manuscript_revisions", to="writer.storyproject")),
            ],
            options={"ordering": ("version",)},
        ),
        migrations.AddField(
            model_name="storyproject",
            name="status",
            field=models.CharField(choices=[("WRITING", "Em escrita"), ("CANONICAL_READY", "Miolo canônico pronto"), ("CLOSED", "Projeto fechado")], default="WRITING", max_length=24),
        ),
        migrations.AddField(
            model_name="storyproject",
            name="canonical_manuscript",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="writer.manuscriptrevision"),
        ),
        migrations.AddField(
            model_name="storyproject",
            name="closed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="storyproject",
            name="closed_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="writer_closed_projects", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddConstraint(
            model_name="manuscriptrevision",
            constraint=models.UniqueConstraint(fields=("project", "version"), name="writer_unique_project_manuscript_revision"),
        ),
    ]
