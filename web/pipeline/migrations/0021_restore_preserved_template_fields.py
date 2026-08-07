"""Restore preserved BookEditionTemplate columns to active Django state.

The physical columns were deliberately retained by migration 0020. Restoring
them to the model makes ORM writes deterministic and prevents NOT NULL
compatibility columns from being silently omitted.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pipeline", "0020_reconcile_legacy_state"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="edition_year",
                    field=models.IntegerField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="edition_copyright_holder",
                    field=models.CharField(blank=True, default="", max_length=120),
                ),
                migrations.AddField(
                    model_name="bookeditiontemplate",
                    name="editorial_name",
                    field=models.CharField(blank=True, default="", max_length=120),
                ),
            ],
        ),
    ]
