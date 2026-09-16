from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("writer", "0008_single_chapter_generation")]
    operations = [
        migrations.AlterField(model_name="chapter", name="status", field=models.CharField(choices=[("PLANNED", "Planejado"), ("GENERATING", "Gerando"), ("MERGING", "Unindo cenas"), ("MERGED", "Merge concluído"), ("GENERATION_COMPLETE", "Geração concluída"), ("FINAL", "Finalizado"), ("FAILED", "Falhou"), ("CONTRACT_READY", "Contrato pronto"), ("CONTRACT_FROZEN", "Contrato congelado"), ("PACKAGE_READY", "Pacote pronto localmente"), ("EXPORTED_TO_DRIVE", "Exportado para Drive"), ("WAITING_FOR_OUTPUT", "Aguardando saída externa"), ("OUTPUT_IMPORTED", "Saída importada"), ("CLOSURE_COMPLETE", "Fechamento concluído")], default="PLANNED", max_length=24)),
        migrations.AlterField(model_name="sourcedocument", name="status", field=models.CharField(choices=[("DISCOVERED", "Descoberto"), ("NORMALIZED", "Normalizado"), ("VECTORIZED", "Vetorizado"), ("FAILED", "Falhou")], default="DISCOVERED", max_length=24)),
        migrations.AddField(model_name="chapter", name="contract_version", field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="chapter", name="contract_json_path", field=models.TextField(blank=True)),
        migrations.AddField(model_name="chapter", name="drive_folder_url", field=models.URLField(blank=True)),
        migrations.AddField(model_name="chapter", name="drive_contract_url", field=models.URLField(blank=True)),
        migrations.AddField(model_name="chapter", name="drive_output_url", field=models.URLField(blank=True)),
        migrations.AddField(model_name="chapter", name="drive_folder_id", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="chapter", name="drive_contract_id", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="chapter", name="drive_json_id", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="chapter", name="drive_output_id", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="chapter", name="drive_export_error", field=models.TextField(blank=True)),
        migrations.AddField(model_name="storyproject", name="anchor_facts", field=models.JSONField(blank=True, default=list)),
        migrations.CreateModel(
            name="NarrativeContract",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.PositiveIntegerField(default=1)), ("schema_version", models.CharField(default="1.0", max_length=20)),
                ("book_kit_snapshot", models.JSONField(default=dict)), ("book_kit_sha256", models.CharField(max_length=64)),
                ("chapter_plan", models.JSONField(default=dict)), ("continuity_snapshot", models.JSONField(default=dict)),
                ("chapter_history", models.JSONField(default=dict)), ("source_rag_context", models.JSONField(default=list)),
                ("narrative_memory_context", models.JSONField(default=list)), ("payload_json", models.JSONField(default=dict)),
                ("payload_sha256", models.CharField(max_length=64)), ("estimated_input_tokens", models.PositiveIntegerField(default=0)),
                ("token_budget", models.JSONField(default=dict)),
                ("status", models.CharField(choices=[("READY", "Pronto"), ("USED", "Usado"), ("SUPERSEDED", "Substituído")], default="READY", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("frozen_at", models.DateTimeField(blank=True, null=True)),
                ("chapter", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="narrative_contracts", to="writer.chapter")),
                ("project", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="narrative_contracts", to="writer.storyproject")),
            ],
            options={"ordering": ("project_id", "chapter_id", "-version")},
        ),
        migrations.CreateModel(
            name="ChapterNarrativeSummary",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.PositiveIntegerField(default=1)), ("short_summary", models.TextField(blank=True)), ("detailed_summary", models.TextField(blank=True)),
                ("opening_state", models.JSONField(default=dict)), ("major_events", models.JSONField(default=list)), ("closing_state", models.JSONField(default=dict)),
                ("characters_present", models.JSONField(default=list)), ("locations", models.JSONField(default=list)), ("facts_established", models.JSONField(default=list)),
                ("new_information", models.JSONField(default=list)), ("resolved_threads", models.JSONField(default=list)), ("open_threads", models.JSONField(default=list)),
                ("continuity_delta", models.JSONField(default=dict)), ("source_contract_sha256", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("chapter", models.OneToOneField(on_delete=models.deletion.CASCADE, related_name="narrative_summary", to="writer.chapter")),
                ("project", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="chapter_summaries", to="writer.storyproject")),
            ],
        ),
        migrations.CreateModel(
            name="StoryContinuityState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("through_chapter", models.PositiveIntegerField()), ("version", models.PositiveIntegerField(default=1)),
                ("state_json", models.JSONField(default=dict)), ("state_sha256", models.CharField(max_length=64)), ("created_at", models.DateTimeField(auto_now_add=True)),
                ("project", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="continuity_states", to="writer.storyproject")),
            ], options={"ordering": ("project_id", "-through_chapter", "-version")},
        ),
        migrations.AddConstraint(model_name="narrativecontract", constraint=models.UniqueConstraint(fields=("project", "chapter", "version"), name="writer_unique_contract_version")),
        migrations.AddConstraint(model_name="storycontinuitystate", constraint=models.UniqueConstraint(fields=("project", "through_chapter", "version"), name="writer_unique_continuity_version")),
    ]
