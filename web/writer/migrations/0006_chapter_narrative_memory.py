from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("writer", "0005_chapter_session_scenes")]

    operations = [
        migrations.AddField(
            model_name="chapter",
            name="merged_text",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="chapter",
            name="merge_model",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="chapter",
            name="merge_input_sha256",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="chapter",
            name="merged_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="chapter",
            name="narrative_state",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="chapter",
            name="narrative_memory_model",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="chapter",
            name="logic_audit",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="chapter",
            name="logic_audit_model",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="chapter",
            name="memory_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="chapter",
            name="status",
            field=models.CharField(
                choices=[
                    ("PLANNED", "Planejado"),
                    ("GENERATING", "Gerando"),
                    ("MERGING", "Unindo cenas"),
                    ("MERGED", "Merge concluído"),
                    ("GENERATION_COMPLETE", "Geração concluída"),
                    ("FINAL", "Finalizado"),
                    ("FAILED", "Falhou"),
                ],
                default="PLANNED",
                max_length=24,
            ),
        ),
    ]
