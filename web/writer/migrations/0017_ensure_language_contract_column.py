from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("writer", "0016_optional_reference_build")]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE writer_storyproject ADD COLUMN IF NOT EXISTS language_contract jsonb NOT NULL DEFAULT '{}'::jsonb",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
