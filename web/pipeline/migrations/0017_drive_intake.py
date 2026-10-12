from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("pipeline", "0016_incremental_editorial_import")]

    operations = [
        migrations.CreateModel(
            name="IntakeCounter",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=32, unique=True)),
                ("next_value", models.PositiveIntegerField(default=1)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["key"]},
        ),
        migrations.CreateModel(
            name="IntakeBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("batch_code", models.CharField(editable=False, max_length=32, unique=True)),
                ("name", models.CharField(max_length=255)),
                ("slug", models.SlugField(max_length=255)),
                ("source", models.CharField(choices=[("UPLOAD", "Upload"), ("GOOGLE_DRIVE", "Google Drive"), ("LOCAL_WATCH", "Local monitored folder")], max_length=20)),
                ("remote", models.CharField(blank=True, default="", max_length=100)),
                ("drive_source_path", models.CharField(blank=True, default="", max_length=1000)),
                ("recursive", models.BooleanField(default=True)),
                ("defaults", models.JSONField(default=dict)),
                ("status", models.CharField(choices=[("DISCOVERED", "Discovered"), ("PREVIEWED", "Previewed"), ("STAGED", "Staged"), ("IMPORTED_RAW", "Imported raw"), ("REGISTERED", "Registered"), ("FAILED_RETRYABLE", "Failed — retryable"), ("CONFLICT", "Conflict"), ("REJECTED", "Rejected")], default="DISCOVERED", max_length=24)),
                ("last_error", models.TextField(blank=True, default="")),
                ("last_summary", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at"],
                "constraints": [models.UniqueConstraint(fields=("source", "remote", "drive_source_path"), name="pipeline_intake_batch_source_path_unique")],
            },
        ),
        migrations.CreateModel(
            name="IntakeItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("remote_file_id", models.CharField(blank=True, default="", max_length=255)),
                ("remote_path", models.CharField(max_length=1000)),
                ("relative_path", models.CharField(max_length=1000)),
                ("original_name", models.CharField(max_length=500)),
                ("size_bytes", models.PositiveBigIntegerField()),
                ("mime_type", models.CharField(blank=True, default="", max_length=255)),
                ("extension", models.CharField(max_length=20)),
                ("remote_version", models.CharField(blank=True, default="", max_length=255)),
                ("sha256", models.CharField(blank=True, default="", max_length=64)),
                ("title", models.CharField(max_length=500)),
                ("author_name", models.CharField(blank=True, default="", max_length=255)),
                ("source_language", models.CharField(max_length=16)),
                ("target_language", models.CharField(blank=True, default="", max_length=16)),
                ("book_code", models.CharField(blank=True, default="", editable=False, max_length=64)),
                ("preview_operation", models.CharField(max_length=16)),
                ("status", models.CharField(choices=[("DISCOVERED", "Discovered"), ("PREVIEWED", "Previewed"), ("STAGED", "Staged"), ("IMPORTED_RAW", "Imported raw"), ("REGISTERED", "Registered"), ("FAILED_RETRYABLE", "Failed — retryable"), ("CONFLICT", "Conflict"), ("REJECTED", "Rejected")], default="DISCOVERED", max_length=24)),
                ("canonical_path", models.CharField(blank=True, default="", max_length=1200)),
                ("last_error", models.TextField(blank=True, default="")),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("metadata", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="pipeline.intakebatch")),
            ],
            options={
                "ordering": ["batch_id", "relative_path"],
                "constraints": [
                    models.UniqueConstraint(fields=("batch", "relative_path"), name="pipeline_intake_item_batch_path_unique"),
                    models.UniqueConstraint(condition=models.Q(("remote_file_id", ""), _negated=True), fields=("batch", "remote_file_id"), name="pipeline_intake_item_batch_remote_unique"),
                    models.UniqueConstraint(condition=models.Q(("book_code", ""), _negated=True), fields=("book_code",), name="pipeline_intake_item_book_code_unique"),
                ],
            },
        ),
        migrations.CreateModel(
            name="IntakeAuditEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("correlation_id", models.CharField(db_index=True, max_length=64)),
                ("operation", models.CharField(max_length=32)),
                ("previous_status", models.CharField(blank=True, default="", max_length=24)),
                ("new_status", models.CharField(max_length=24)),
                ("attempt", models.PositiveIntegerField(default=1)),
                ("detail", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="audit_events", to="pipeline.intakebatch")),
                ("item", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_events", to="pipeline.intakeitem")),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
    ]
