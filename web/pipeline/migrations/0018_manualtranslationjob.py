from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0017_editionpipeline_last_version"),
        ("pipeline", "0017_drive_intake"),
    ]

    operations = [
        migrations.CreateModel(
            name="ManualTranslationJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_language", models.CharField(max_length=16)),
                ("target_language", models.CharField(max_length=16)),
                ("drive_path", models.CharField(max_length=1000)),
                ("source_path", models.CharField(max_length=1200)),
                ("source_sha256", models.CharField(max_length=64)),
                ("expected_return_name", models.CharField(max_length=500)),
                ("status", models.CharField(choices=[("EXPORTED", "Aguardando retorno"), ("IMPORTED", "Tradução importada"), ("FAILED", "Falha recuperável")], default="EXPORTED", max_length=16)),
                ("return_source", models.CharField(blank=True, default="", max_length=1200)),
                ("return_sha256", models.CharField(blank=True, default="", max_length=64)),
                ("last_error", models.TextField(blank=True, default="")),
                ("exported_at", models.DateTimeField(auto_now_add=True)),
                ("imported_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("edition", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="manual_translation_jobs", to="editorial.edition")),
                ("target_edition", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="manual_translation_returns", to="editorial.edition")),
            ],
            options={"ordering": ["-updated_at", "id"]},
        ),
        migrations.AddConstraint(
            model_name="manualtranslationjob",
            constraint=models.UniqueConstraint(fields=("edition", "target_language"), name="pipeline_manual_translation_edition_target_unique"),
        ),
    ]
