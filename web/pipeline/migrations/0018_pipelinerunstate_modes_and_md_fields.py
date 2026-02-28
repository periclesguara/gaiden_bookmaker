from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pipeline", "0017_pipelinerunstate"),
    ]

    operations = [
        migrations.AddField(
            model_name="pipelinerunstate",
            name="effective_mode",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="pipelinerunstate",
            name="md_generated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pipelinerunstate",
            name="md_source_sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="pipelinerunstate",
            name="md_status",
            field=models.CharField(blank=True, default="", max_length=30),
        ),
        migrations.AddField(
            model_name="pipelinerunstate",
            name="refine_mode",
            field=models.CharField(blank=True, default="do", max_length=10),
        ),
        migrations.AddField(
            model_name="pipelinerunstate",
            name="selected_mode",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="pipelinerunstate",
            name="split_mode",
            field=models.CharField(blank=True, default="do", max_length=10),
        ),
        migrations.AddField(
            model_name="pipelinerunstate",
            name="warnings",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
