from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="IntakeBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.SlugField(unique=True)),
                ("name", models.CharField(max_length=255)),
                ("author_default", models.CharField(blank=True, max_length=255)),
                ("source_language", models.CharField(max_length=20)),
                ("imprint_default", models.CharField(blank=True, max_length=255)),
                ("editor_default", models.CharField(blank=True, max_length=255)),
                ("collection_name", models.CharField(blank=True, max_length=255)),
                ("public_domain", models.BooleanField(default=False)),
                ("drive_folder_id", models.CharField(blank=True, max_length=255)),
                ("drive_relative_path", models.CharField(blank=True, max_length=500)),
                ("status", models.CharField(choices=[("DISCOVERED", "Discovered"), ("DOWNLOADING", "Downloading"), ("DOWNLOADED", "Downloaded"), ("CLEANING", "Cleaning"), ("CLEAN_READY", "Clean Ready"), ("READY_FOR_CODEX", "Ready For Codex"), ("TRANSLATING", "Translating"), ("TRANSLATION_RETURNED", "Translation Returned"), ("READY_FOR_EDITING", "Ready For Editing"), ("FAILED", "Failed")], default="DISCOVERED", max_length=32)),
                ("last_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="IntakeItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order_index", models.PositiveIntegerField()),
                ("drive_file_id", models.CharField(blank=True, max_length=255)),
                ("source_filename", models.CharField(max_length=255)),
                ("source_format", models.CharField(max_length=10)),
                ("source_size", models.PositiveBigIntegerField(default=0)),
                ("source_sha256", models.CharField(blank=True, max_length=64)),
                ("suggested_title", models.CharField(blank=True, max_length=255)),
                ("confirmed_title", models.CharField(blank=True, max_length=255)),
                ("original_year", models.PositiveIntegerField(blank=True, null=True)),
                ("target_language", models.CharField(blank=True, max_length=20)),
                ("book_code", models.SlugField(blank=True)),
                ("original_path", models.CharField(blank=True, max_length=500)),
                ("clean_path", models.CharField(blank=True, max_length=500)),
                ("translation_input_path", models.CharField(blank=True, max_length=500)),
                ("translation_return_path", models.CharField(blank=True, max_length=500)),
                ("status", models.CharField(choices=[("DISCOVERED", "Discovered"), ("DOWNLOADING", "Downloading"), ("DOWNLOADED", "Downloaded"), ("CLEANING", "Cleaning"), ("CLEAN_READY", "Clean Ready"), ("READY_FOR_CODEX", "Ready For Codex"), ("TRANSLATING", "Translating"), ("TRANSLATION_RETURNED", "Translation Returned"), ("READY_FOR_EDITING", "Ready For Editing"), ("FAILED", "Failed")], default="DISCOVERED", max_length=32)),
                ("last_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="intake_module.intakebatch")),
            ],
            options={"ordering": ["order_index", "id"]},
        ),
        migrations.AddConstraint(
            model_name="intakeitem",
            constraint=models.UniqueConstraint(fields=("batch", "order_index"), name="intake_unique_item_order_per_batch"),
        ),
    ]
