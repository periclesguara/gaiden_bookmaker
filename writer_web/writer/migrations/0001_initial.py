from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SourceDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("filename", models.CharField(max_length=255)),
                ("source_path", models.TextField(unique=True)),
                ("source_sha256", models.CharField(blank=True, max_length=64)),
                ("normalized_path", models.TextField(blank=True)),
                ("normalized_sha256", models.CharField(blank=True, max_length=64)),
                ("provider", models.CharField(blank=True, max_length=40)),
                ("status", models.CharField(choices=[("DISCOVERED", "Descoberto"), ("NORMALIZED", "Normalizado"), ("VECTORIZED", "Vetorizado"), ("FAILED", "Falhou")], default="DISCOVERED", max_length=16)),
                ("normalization_report", models.JSONField(blank=True, default=dict)),
                ("error_message", models.TextField(blank=True)),
                ("discovered_at", models.DateTimeField(auto_now_add=True)),
                ("normalized_at", models.DateTimeField(blank=True, null=True)),
                ("vectorized_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ("filename", "id")},
        ),
        migrations.CreateModel(
            name="StoryProject",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("language", models.CharField(default="Português (Brasil)", max_length=40)),
                ("premise", models.TextField(blank=True)),
                ("character_bible", models.TextField(blank=True)),
                ("antagonist_bible", models.TextField(blank=True)),
                ("scenario_bible", models.TextField(blank=True)),
                ("world_bible", models.TextField(blank=True)),
                ("story_direction", models.TextField(blank=True)),
                ("story_outline", models.TextField(blank=True)),
                ("chapter_count", models.PositiveSmallIntegerField(default=10, validators=[MinValueValidator(1), MaxValueValidator(100)])),
                ("vector_index_path", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("sources", models.ManyToManyField(blank=True, related_name="projects", to="writer.sourcedocument")),
            ],
            options={"ordering": ("-updated_at", "title")},
        ),
        migrations.CreateModel(
            name="Chapter",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("number", models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])),
                ("title", models.CharField(blank=True, max_length=255)),
                ("direction", models.TextField(blank=True)),
                ("script", models.TextField(blank=True)),
                ("target_words", models.PositiveIntegerField(default=2500, validators=[MinValueValidator(400), MaxValueValidator(12000)])),
                ("session_count", models.PositiveSmallIntegerField(default=4, validators=[MinValueValidator(1), MaxValueValidator(4)])),
                ("retrieval_top_k", models.PositiveSmallIntegerField(default=8, validators=[MinValueValidator(1), MaxValueValidator(20)])),
                ("status", models.CharField(choices=[("PLANNED", "Planejado"), ("GENERATING", "Gerando"), ("GENERATION_COMPLETE", "Geração concluída"), ("FINAL", "Finalizado"), ("FAILED", "Falhou")], default="PLANNED", max_length=24)),
                ("final_text", models.TextField(blank=True)),
                ("error_message", models.TextField(blank=True)),
                ("finalized_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="chapters", to="writer.storyproject")),
            ],
            options={"ordering": ("project_id", "number")},
        ),
        migrations.CreateModel(
            name="ChapterSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("number", models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(4)])),
                ("status", models.CharField(choices=[("COMPLETE", "Concluída"), ("FAILED", "Falhou")], max_length=12)),
                ("content", models.TextField(blank=True)),
                ("word_count", models.PositiveIntegerField(default=0)),
                ("model", models.CharField(blank=True, max_length=160)),
                ("source_chunk_ids", models.JSONField(blank=True, default=list)),
                ("source_scores", models.JSONField(blank=True, default=list)),
                ("generation_parameters", models.JSONField(blank=True, default=dict)),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("chapter", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sessions", to="writer.chapter")),
            ],
            options={"ordering": ("chapter_id", "number")},
        ),
        migrations.AddConstraint(
            model_name="chapter",
            constraint=models.UniqueConstraint(fields=("project", "number"), name="writer_unique_project_chapter"),
        ),
        migrations.AddConstraint(
            model_name="chaptersession",
            constraint=models.UniqueConstraint(fields=("chapter", "number"), name="writer_unique_chapter_session"),
        ),
    ]
