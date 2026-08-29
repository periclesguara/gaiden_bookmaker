from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0022_alter_pipelineartifact_stage"),
    ]

    operations = [
        migrations.AddField(
            model_name="editionbuild",
            name="epubcheck_status",
            field=models.CharField(
                choices=[
                    ("EPUBCHECK_PENDING", "EPUBCheck pending"),
                    ("EPUBCHECK_RUNNING", "EPUBCheck running"),
                    ("EPUBCHECK_PASSED", "EPUBCheck passed"),
                    ("EPUBCHECK_PASSED_WITH_WARNINGS", "EPUBCheck passed with warnings"),
                    ("EPUBCHECK_FAILED", "EPUBCheck failed"),
                    ("EPUBCHECK_UNAVAILABLE", "EPUBCheck unavailable"),
                ],
                db_index=True,
                default="EPUBCHECK_PENDING",
                max_length=40,
            ),
        ),
        migrations.AddField(model_name="editionbuild", name="epubcheck_version", field=models.CharField(blank=True, default="", max_length=200)),
        migrations.AddField(model_name="editionbuild", name="epubcheck_run_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="editionbuild", name="epubcheck_returncode", field=models.IntegerField(blank=True, null=True)),
        migrations.AddField(model_name="editionbuild", name="epubcheck_fatal_count", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="editionbuild", name="epubcheck_error_count", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="editionbuild", name="epubcheck_warning_count", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="editionbuild", name="epubcheck_validated_sha256", field=models.CharField(blank=True, db_index=True, default="", max_length=64)),
        migrations.AddField(model_name="editionbuild", name="epubcheck_report_path", field=models.CharField(blank=True, default="", max_length=500)),
        migrations.AddField(model_name="editionbuild", name="epubcheck_report_sha256", field=models.CharField(blank=True, default="", max_length=64)),
    ]
