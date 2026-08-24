"""Keep the active Intake model compatible with preserved provenance storage.

The operational database may already contain this column through the approved
source-provenance migration. The guarded SQL also supports clean databases from
this branch without deleting or rewriting an existing provenance record.
"""

from django.db import migrations, models


ADD_PROVENANCE_COLUMN = """
ALTER TABLE work
    ADD COLUMN IF NOT EXISTS source_provenance jsonb;
UPDATE work
    SET source_provenance = '{}'::jsonb
    WHERE source_provenance IS NULL;
ALTER TABLE work
    ALTER COLUMN source_provenance SET NOT NULL;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0019_editionbuild_validation_report"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=ADD_PROVENANCE_COLUMN,
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="work",
                    name="source_provenance",
                    field=models.JSONField(blank=True, default=dict),
                ),
            ],
        ),
    ]
