"""Reconcile pipeline migration state without deleting legacy columns.

The active main models no longer expose three historical template fields.
Their physical columns are preserved for canonical database compatibility;
only Django's project state is aligned with the checked-in models.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pipeline", "0019_alter_pipelinerun_action"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    model_name="bookeditiontemplate",
                    name="edition_copyright_holder",
                ),
                migrations.RemoveField(
                    model_name="bookeditiontemplate",
                    name="edition_year",
                ),
                migrations.RemoveField(
                    model_name="bookeditiontemplate",
                    name="editorial_name",
                ),
                migrations.AlterField(
                    model_name="bookeditiontemplate",
                    name="language",
                    field=models.CharField(
                        choices=[
                            ("en", "en"),
                            ("es", "es"),
                            ("ptbr", "pt-br"),
                            ("de", "Deutsch"),
                        ],
                        default="en",
                        max_length=8,
                    ),
                ),
                migrations.AlterField(
                    model_name="pipelinejob",
                    name="stage",
                    field=models.CharField(
                        choices=[
                            ("raw", "Raw"),
                            ("normalize", "Normalize"),
                            ("split", "Split"),
                            ("translate", "Translate"),
                            ("refine", "Refine"),
                            ("polish", "Polish"),
                        ],
                        max_length=50,
                    ),
                ),
                migrations.AlterField(
                    model_name="pipelinerun",
                    name="action",
                    field=models.CharField(
                        choices=[
                            ("NORMALIZE", "Normalize"),
                            ("CHUNK", "Chunk"),
                            ("TRANSLATE", "Translate"),
                            ("TRANSLATE_DEFAULT", "Translate Default"),
                            ("SPLIT_FOR_REFINE", "Split for Refine"),
                            ("RETURN_REFINE", "Return Refine"),
                            ("BUILD", "Build"),
                            ("EXPORT_EPUB", "Export EPUB"),
                        ],
                        default="TRANSLATE",
                        max_length=30,
                    ),
                ),
            ],
        ),
    ]
