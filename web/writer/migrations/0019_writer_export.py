from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("writer", "0018_canonical_reference_preparation"),
    ]

    operations = [
        migrations.CreateModel(
            name="WriterExport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("export_id", models.CharField(max_length=64, unique=True)),
                ("status", models.CharField(choices=[("CREATING", "Criando"), ("READY", "Pronto"), ("FAILED", "Falhou")], default="CREATING", max_length=16)),
                ("export_folder_id", models.CharField(blank=True, max_length=255)),
                ("export_folder_url", models.URLField(blank=True)),
                ("return_folder_id", models.CharField(blank=True, max_length=255)),
                ("return_folder_url", models.URLField(blank=True)),
                ("manifest_file_id", models.CharField(blank=True, max_length=255)),
                ("manifest_file_url", models.URLField(blank=True)),
                ("contract_file_id", models.CharField(blank=True, max_length=255)),
                ("contract_file_url", models.URLField(blank=True)),
                ("readme_file_id", models.CharField(blank=True, max_length=255)),
                ("readme_file_url", models.URLField(blank=True)),
                ("expected_files", models.JSONField(blank=True, default=list)),
                ("manifest_json", models.JSONField(blank=True, default=dict)),
                ("clipboard_contract", models.TextField(blank=True)),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("ready_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="writer_exports", to="writer.storyproject")),
            ],
            options={
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddConstraint(
            model_name="writerexport",
            constraint=models.UniqueConstraint(fields=("project", "export_id"), name="writer_unique_project_export_id"),
        ),
    ]
