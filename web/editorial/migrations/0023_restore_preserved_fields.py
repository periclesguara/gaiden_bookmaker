"""Restore preserved legacy columns to the active Django model state.

Migration 0022 intentionally kept these physical columns while removing them
from Django state. They include NOT NULL columns without persistent database
defaults, so omitting them from ORM INSERT statements breaks normal writes.
The columns already exist; only Django state is restored here.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0022_reconcile_legacy_state"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AddField(
                    model_name="work",
                    name="subtitle",
                    field=models.CharField(blank=True, max_length=255),
                ),
                migrations.AddField(
                    model_name="work",
                    name="enabled_languages",
                    field=models.JSONField(blank=True, default=list),
                ),
                migrations.AddField(
                    model_name="work",
                    name="source_format",
                    field=models.CharField(
                        choices=[("TXT", "TXT"), ("MD", "MD")],
                        default="TXT",
                        max_length=10,
                    ),
                ),
                migrations.AddField(
                    model_name="work",
                    name="notes",
                    field=models.TextField(blank=True),
                ),
                migrations.AddField(
                    model_name="edition",
                    name="introduction_text",
                    field=models.TextField(blank=True, default="", null=True),
                ),
                migrations.AddField(
                    model_name="edition",
                    name="epilogue_text",
                    field=models.TextField(blank=True, default="", null=True),
                ),
                migrations.AddField(
                    model_name="edition",
                    name="language_variant",
                    field=models.CharField(blank=True, default="", max_length=20),
                ),
                migrations.AddField(
                    model_name="edition",
                    name="copyright_text",
                    field=models.TextField(blank=True, default=""),
                ),
                migrations.AddField(
                    model_name="edition",
                    name="editorial_name",
                    field=models.CharField(blank=True, default="", max_length=120),
                ),
                migrations.AddField(
                    model_name="edition",
                    name="edition_copyright_holder",
                    field=models.CharField(blank=True, default="", max_length=120),
                ),
                migrations.AddField(
                    model_name="pipelineartifact",
                    name="status",
                    field=models.CharField(default="OK", max_length=16),
                ),
                migrations.AddField(
                    model_name="pipelineartifact",
                    name="sha256",
                    field=models.CharField(blank=True, default="", max_length=64),
                ),
            ],
        ),
    ]
