from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("writer", "0006_chapter_narrative_memory")]

    operations = [
        migrations.AddField(
            model_name="sourcedocument", name="source_type",
            field=models.CharField(choices=[("LOCAL_FILE", "Arquivo local"), ("GOOGLE_DOC", "Google Docs")], default="LOCAL_FILE", max_length=24),
        ),
        migrations.AddField(model_name="sourcedocument", name="external_id", field=models.CharField(blank=True, db_index=True, max_length=255)),
        migrations.AddField(model_name="sourcedocument", name="external_url", field=models.URLField(blank=True)),
        migrations.AddField(model_name="sourcedocument", name="external_revision_id", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="sourcedocument", name="external_modified_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AlterField(model_name="sourcedocument", name="source_path", field=models.TextField()),
        migrations.AddConstraint(
            model_name="sourcedocument",
            constraint=models.UniqueConstraint(
                condition=Q(source_type="GOOGLE_DOC", external_id__gt=""),
                fields=("source_type", "external_id"),
                name="writer_unique_google_doc_source",
            ),
        ),
    ]
