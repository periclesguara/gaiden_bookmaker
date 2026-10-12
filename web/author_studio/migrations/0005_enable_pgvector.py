from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("author_studio", "0004_chunk_processing_stabilization"),
    ]

    operations = [
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS vector",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
