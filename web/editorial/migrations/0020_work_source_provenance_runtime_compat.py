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


def ensure_provenance_column(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(ADD_PROVENANCE_COLUMN)
        return
    if schema_editor.connection.vendor == "sqlite":
        columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                schema_editor.connection.cursor(),
                "work",
            )
        }
        if "source_provenance" not in columns:
            schema_editor.execute(
                "ALTER TABLE work ADD COLUMN source_provenance text "
                "NOT NULL DEFAULT '{}'"
            )
        return
    raise RuntimeError(
        "The Work provenance compatibility migration supports PostgreSQL and SQLite only."
    )


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0019_editionbuild_validation_report"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    code=ensure_provenance_column,
                    reverse_code=migrations.RunPython.noop,
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
