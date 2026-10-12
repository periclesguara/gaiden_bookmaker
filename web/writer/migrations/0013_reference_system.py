from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("editorial", "0023_editionbuild_epubcheck_gate"), ("writer", "0012_manuscript_drive_pipeline")]

    operations = [
        migrations.AddField(
            model_name="storyproject", name="reference_bible", field=models.TextField(blank=True)
        ),
        migrations.CreateModel(
            name="ProjectReference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enabled", models.BooleanField(default=True)),
                ("roles", models.JSONField(blank=True, default=list)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="references", to="writer.storyproject")),
                ("source", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="writer_project_references", to="editorial.editionbuild")),
            ],
            options={"ordering": ("source__edition__work__title", "id")},
        ),
        migrations.AddConstraint(
            model_name="projectreference",
            constraint=models.UniqueConstraint(fields=("project", "source"), name="writer_unique_project_reference"),
        ),
        migrations.CreateModel(
            name="SourceVectorIndex",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_hash", models.CharField(blank=True, max_length=64)),
                ("embedding_model", models.CharField(blank=True, max_length=160)),
                ("embedding_version", models.CharField(default="1", max_length=40)),
                ("chunking_version", models.CharField(default="writer-source-v1", max_length=40)),
                ("chunk_count", models.PositiveIntegerField(default=0)),
                ("status", models.CharField(choices=[("NOT_INDEXED", "Não vetorizado"), ("QUEUED", "Na fila"), ("PROCESSING", "Processando"), ("READY", "Pronto"), ("FAILED", "Falhou"), ("OUTDATED", "Desatualizado")], default="NOT_INDEXED", max_length=20)),
                ("index_path", models.TextField(blank=True)),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("source", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="writer_vector_index", to="editorial.editionbuild")),
            ],
        ),
    ]
