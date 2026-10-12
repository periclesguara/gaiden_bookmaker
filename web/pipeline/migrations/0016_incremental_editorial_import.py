from django.db import migrations, models
import django.db.models.deletion


EDITORIAL_STATUS_CHOICES = [
    ("DRAFT", "Draft"),
    ("READY", "Ready"),
    ("IMPORTED", "Imported"),
    ("IN_PROGRESS", "In progress"),
    ("RETURNED", "Returned"),
    ("APPROVED", "Approved"),
    ("FAILED", "Failed"),
    ("SUPERSEDED", "Superseded"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0017_editionpipeline_last_version"),
        ("pipeline", "0015_bookeditiontemplate_epilogue_text_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="IncrementalEdition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("edition_id", models.CharField(max_length=255, unique=True)),
                ("work_id", models.CharField(max_length=255)),
                ("book_code", models.CharField(db_index=True, max_length=64)),
                ("locale", models.CharField(max_length=16)),
                ("expected_block_count", models.PositiveIntegerField()),
                ("status", models.CharField(choices=EDITORIAL_STATUS_CHOICES, default="DRAFT", max_length=20)),
                ("last_contiguous_sequence", models.PositiveIntegerField(default=0)),
                ("next_sequence", models.PositiveIntegerField(blank=True, null=True)),
                ("confirmed_block_id", models.CharField(blank=True, default="", max_length=255)),
                ("manifest_sha256", models.CharField(blank=True, default="", max_length=64)),
                ("last_import_run_id", models.CharField(blank=True, default="", max_length=64)),
                ("drive_destination", models.CharField(blank=True, default="", max_length=1000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "editorial_edition",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="incremental_editions",
                        to="editorial.edition",
                    ),
                ),
            ],
            options={"ordering": ["edition_id"]},
        ),
        migrations.CreateModel(
            name="IncrementalBlock",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("block_id", models.CharField(max_length=500)),
                ("sequence", models.PositiveIntegerField()),
                ("version", models.PositiveIntegerField(default=1)),
                ("file_name", models.CharField(max_length=500)),
                ("content", models.TextField()),
                ("content_sha256", models.CharField(max_length=64)),
                ("size_bytes", models.PositiveBigIntegerField()),
                ("status", models.CharField(choices=EDITORIAL_STATUS_CHOICES, default="IMPORTED", max_length=20)),
                ("source_block_id", models.CharField(blank=True, default="", max_length=500)),
                ("source_updated_at", models.DateTimeField(blank=True, null=True)),
                ("is_current", models.BooleanField(default=True)),
                ("exported_sha256", models.CharField(blank=True, default="", max_length=64)),
                ("exported_status", models.CharField(blank=True, default="", max_length=20)),
                ("exported_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "edition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="blocks",
                        to="pipeline.incrementaledition",
                    ),
                ),
            ],
            options={
                "ordering": ["edition_id", "sequence", "version"],
                "constraints": [
                    models.UniqueConstraint(fields=("edition", "block_id", "version"), name="pipeline_incremental_block_version_unique"),
                    models.UniqueConstraint(fields=("edition", "sequence", "version"), name="pipeline_incremental_sequence_version_unique"),
                    models.UniqueConstraint(condition=models.Q(("is_current", True)), fields=("edition", "block_id"), name="pipeline_incremental_current_block_unique"),
                    models.UniqueConstraint(condition=models.Q(("is_current", True)), fields=("edition", "sequence"), name="pipeline_incremental_current_sequence_unique"),
                ],
            },
        ),
        migrations.CreateModel(
            name="IncrementalImportRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("run_id", models.CharField(max_length=64, unique=True)),
                ("job_id", models.CharField(max_length=255)),
                ("manifest_sha256", models.CharField(max_length=64)),
                ("import_attempt", models.PositiveIntegerField(default=1)),
                ("status", models.CharField(choices=[("RUNNING", "Running"), ("SUCCESS", "Success"), ("PARTIAL", "Partial"), ("FAILED", "Failed")], default="RUNNING", max_length=20)),
                ("manifest", models.JSONField(default=dict)),
                ("result", models.JSONField(default=dict)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "edition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="import_runs",
                        to="pipeline.incrementaledition",
                    ),
                ),
            ],
            options={
                "ordering": ["-started_at"],
                "constraints": [
                    models.UniqueConstraint(fields=("job_id", "manifest_sha256", "import_attempt"), name="pipeline_incremental_import_idempotency_unique")
                ],
            },
        ),
        migrations.CreateModel(
            name="IncrementalImportEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sequence", models.PositiveIntegerField()),
                ("block_id", models.CharField(max_length=500)),
                ("action", models.CharField(max_length=40)),
                ("detail", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "block_version",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="import_events",
                        to="pipeline.incrementalblock",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="events",
                        to="pipeline.incrementalimportrun",
                    ),
                ),
            ],
            options={"ordering": ["run_id", "sequence", "id"]},
        ),
    ]
