import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("intake_module", "0004_translationjob_and_identity_constraints"),
        ("pipeline", "0015_bookeditiontemplate_epilogue_text_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="OfficialBodySnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sha256", models.CharField(max_length=64)),
                ("size", models.PositiveBigIntegerField()),
                ("relative_path", models.CharField(max_length=500)),
                ("provenance", models.CharField(choices=[("internal_polish", "Internal polish"), ("drive_official", "Drive official"), ("manual_editorial_approval", "Manual editorial approval")], max_length=40)),
                ("source_stage", models.CharField(max_length=40)),
                ("is_active", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("superseded_at", models.DateTimeField(blank=True, null=True)),
                ("edition", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="official_body_snapshots", to="editorial.edition")),
                ("translation_job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="official_snapshots", to="intake_module.translationjob")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="OfficialBodyPromotion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("operation_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("state", models.CharField(choices=[("PREPARED", "PREPARED"), ("FILE_STAGED", "FILE_STAGED"), ("DB_COMMITTED", "DB_COMMITTED"), ("CANONICAL_PUBLISHED", "CANONICAL_PUBLISHED"), ("COMPLETED", "COMPLETED"), ("FAILED", "FAILED")], default="PREPARED", max_length=32)),
                ("input_sha256", models.CharField(blank=True, max_length=64)),
                ("return_sha256", models.CharField(max_length=64)),
                ("staged_path", models.CharField(blank=True, max_length=500)),
                ("previous_canonical_sha256", models.CharField(blank=True, max_length=64)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("edition", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="official_body_promotions", to="editorial.edition")),
                ("new_snapshot", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="promotion_operation", to="pipeline.officialbodysnapshot")),
                ("previous_snapshot", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="superseded_by_operations", to="pipeline.officialbodysnapshot")),
                ("translation_job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="promotion_operations", to="intake_module.translationjob")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="officialbodysnapshot",
            constraint=models.UniqueConstraint(condition=models.Q(is_active=True), fields=("edition",), name="pipeline_one_active_official_body"),
        ),
        migrations.AddConstraint(
            model_name="officialbodysnapshot",
            constraint=models.UniqueConstraint(fields=("edition", "sha256"), name="pipeline_unique_official_body_sha"),
        ),
    ]
