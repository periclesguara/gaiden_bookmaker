import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0016_editionpipeline_build_outdated_and_more"),
        ("intake_module", "0003_intakeitem_duplicate_of"),
    ]

    operations = [
        migrations.CreateModel(
            name="TranslationJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("job_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("schema_version", models.PositiveSmallIntegerField(default=2)),
                ("source_language", models.CharField(max_length=10)),
                ("target_language", models.CharField(max_length=10)),
                ("output_stage", models.CharField(choices=[("translated", "Translated intermediate"), ("official", "Editorial final")], max_length=16)),
                ("confirmed_title", models.CharField(max_length=255)),
                ("frozen_title_slug", models.SlugField(max_length=255)),
                ("input_folder", models.CharField(max_length=500)),
                ("input_filename", models.CharField(max_length=255)),
                ("input_sha256", models.CharField(max_length=64)),
                ("expected_return_folder", models.CharField(max_length=500)),
                ("expected_return_filename", models.CharField(max_length=255)),
                ("manifest_filename", models.CharField(max_length=255)),
                ("manifest_path", models.CharField(max_length=500)),
                ("status", models.CharField(choices=[("EXPORTED", "Exported"), ("RETURN_PENDING", "Return pending"), ("VALIDATED", "Validated"), ("COMPLETED", "Completed"), ("SUPERSEDED", "Superseded"), ("FAILED", "Failed")], default="EXPORTED", max_length=24)),
                ("return_sha256", models.CharField(blank=True, max_length=64)),
                ("validation_status", models.CharField(blank=True, max_length=32)),
                ("validation_report_path", models.CharField(blank=True, max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("superseded_at", models.DateTimeField(blank=True, null=True)),
                ("edition", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="translation_jobs", to="editorial.edition")),
                ("intake_item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="translation_jobs", to="intake_module.intakeitem")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="intakeitem",
            constraint=models.UniqueConstraint(condition=~models.Q(book_code=""), fields=("book_code",), name="intake_unique_nonempty_book_code"),
        ),
        migrations.AddConstraint(
            model_name="intakeitem",
            constraint=models.UniqueConstraint(condition=models.Q(handoff_edition_id__isnull=False), fields=("handoff_edition_id",), name="intake_unique_handoff_edition"),
        ),
        migrations.AddConstraint(
            model_name="translationjob",
            constraint=models.UniqueConstraint(fields=("edition", "intake_item", "target_language", "output_stage", "input_sha256"), name="intake_unique_translation_job_input"),
        ),
    ]
