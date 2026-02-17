from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("editorial", "0021_alter_edition_language_code_and_more"),
        ("pipeline", "0016_alter_pipelinerun_action"),
    ]

    operations = [
        migrations.CreateModel(
            name="PipelineRunState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("asset_language", models.CharField(blank=True, default="", max_length=10)),
                ("cover_jpg_path", models.CharField(blank=True, default="", max_length=500)),
                ("images_converted_count", models.IntegerField(default=0)),
                ("inserted_images_count", models.IntegerField(default=0)),
                ("last_image_conversion_ts", models.DateTimeField(blank=True, null=True)),
                ("md_path", models.CharField(blank=True, default="", max_length=500)),
                ("build_outputs", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(blank=True, default="", max_length=20)),
                ("last_step", models.CharField(blank=True, default="", max_length=60)),
                ("last_build_ts", models.DateTimeField(blank=True, null=True)),
                ("active_artifact_filename", models.CharField(blank=True, default="", max_length=255)),
                ("last_log", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "edition",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pipeline_run_state",
                        to="editorial.edition",
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at"],
            },
        ),
    ]
