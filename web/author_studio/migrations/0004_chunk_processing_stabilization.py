import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("author_studio", "0003_correct_acd_work_titles"),
    ]

    operations = [
        migrations.AddField(
            model_name="worksplit",
            name="chunker_version",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="worksplit",
            name="maximum_tokens",
            field=models.PositiveIntegerField(default=900),
        ),
        migrations.AddField(
            model_name="worksplit",
            name="minimum_tokens",
            field=models.PositiveIntegerField(default=400),
        ),
        migrations.AddField(
            model_name="worksplit",
            name="overlap_tokens",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="worksplit",
            name="target_tokens",
            field=models.PositiveIntegerField(default=700),
        ),
        migrations.AddField(
            model_name="worksplit",
            name="tokenizer_name",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="workchunk",
            name="chunker_version",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="workchunk",
            name="end_line",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="workchunk",
            name="start_line",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="workchunk",
            name="token_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="workchunk",
            name="tokenizer_name",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.CreateModel(
            name="WorkSplitRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_sha256", models.CharField(db_index=True, max_length=64)),
                ("chunker_version", models.CharField(max_length=50)),
                ("tokenizer_name", models.CharField(max_length=100)),
                ("minimum_tokens", models.PositiveIntegerField(default=400)),
                ("target_tokens", models.PositiveIntegerField(default=700)),
                ("maximum_tokens", models.PositiveIntegerField(default=900)),
                ("overlap_tokens", models.PositiveIntegerField(default=0)),
                ("status", models.CharField(choices=[("PENDING", "Pending"), ("RUNNING", "Running"), ("COMPLETED", "Completed"), ("FAILED", "Failed")], default="PENDING", max_length=20)),
                ("outcome", models.CharField(blank=True, choices=[("CREATED", "Created"), ("REPROCESSED", "Reprocessed"), ("ALREADY_CURRENT", "Already Current"), ("FAILED", "Failed")], max_length=30)),
                ("chunks_previous", models.PositiveIntegerField(default=0)),
                ("chunks_created", models.PositiveIntegerField(default=0)),
                ("chunks_updated", models.PositiveIntegerField(default=0)),
                ("chunks_preserved", models.PositiveIntegerField(default=0)),
                ("chunks_removed", models.PositiveIntegerField(default=0)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("error", models.TextField(blank=True)),
                ("canonical_text", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="processing_runs", to="author_studio.canonicaltext")),
                ("work", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="split_runs", to="author_studio.work")),
            ],
            options={
                "ordering": ["-started_at", "-id"],
            },
        ),
    ]
