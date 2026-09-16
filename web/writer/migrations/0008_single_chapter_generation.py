from django.db import migrations, models


def migrate_sessions(apps, schema_editor):
    Chapter = apps.get_model("writer", "Chapter")
    ChapterSession = apps.get_model("writer", "ChapterSession")
    for chapter in Chapter.objects.all().iterator():
        sessions = ChapterSession.objects.filter(chapter_id=chapter.pk).order_by("number")
        contents = [session.content.strip() for session in sessions if session.content.strip()]
        if not contents:
            continue
        updates = {
            "migrated_from_sessions": True,
            "legacy_sessions": [
                {"number": session.number, "model": session.model,
                 "word_count": session.word_count,
                 "created_at": session.created_at.isoformat() if session.created_at else ""}
                for session in sessions
            ],
        }
        if not chapter.generated_text:
            chapter.generated_text = "\n\n".join(contents)
            chapter.generation_metadata = {**(chapter.generation_metadata or {}), **updates}
            chapter.generation_model = next((s.model for s in sessions if s.model), "")
            chapter.generated_at = chapter.updated_at
            chapter.status = "GENERATION_COMPLETE"
            chapter.save(update_fields=("generated_text", "generation_metadata", "generation_model", "generated_at", "status"))


class Migration(migrations.Migration):
    dependencies = [("writer", "0007_google_docs_source")]
    operations = [
        migrations.AddField(model_name="chapter", name="generated_text", field=models.TextField(blank=True)),
        migrations.AddField(model_name="chapter", name="generation_model", field=models.CharField(blank=True, max_length=160)),
        migrations.AddField(model_name="chapter", name="generation_parameters", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="chapter", name="generation_source_chunk_ids", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="chapter", name="generation_source_scores", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="chapter", name="generation_contract_sha256", field=models.CharField(blank=True, max_length=64)),
        migrations.AddField(model_name="chapter", name="generation_metadata", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="chapter", name="generated_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.RunPython(migrate_sessions, migrations.RunPython.noop),
    ]
