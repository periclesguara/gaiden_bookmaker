"""Reconcile Django state with the active main models without dropping legacy data.

The historical 0013-0021 sources were recovered verbatim from integrate/runner.
Several columns and the EditionBlock table are intentionally absent from the
active main models, but may contain canonical historical data.  State-only
operations preserve those database objects while making future migrations
deterministic.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("editorial", "0021_alter_edition_language_code_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(model_name="edition", name="copyright_text"),
                migrations.RemoveField(model_name="edition", name="edition_copyright_holder"),
                migrations.RemoveField(model_name="edition", name="editorial_name"),
                migrations.RemoveField(model_name="edition", name="epilogue_text"),
                migrations.RemoveField(model_name="edition", name="introduction_text"),
                migrations.RemoveField(model_name="edition", name="language_variant"),
                migrations.RemoveField(model_name="pipelineartifact", name="sha256"),
                migrations.RemoveField(model_name="pipelineartifact", name="status"),
                migrations.RemoveField(model_name="work", name="enabled_languages"),
                migrations.RemoveField(model_name="work", name="notes"),
                migrations.RemoveField(model_name="work", name="source_format"),
                migrations.RemoveField(model_name="work", name="subtitle"),
                migrations.AlterField(
                    model_name="edition",
                    name="language_code",
                    field=models.CharField(
                        choices=[
                            ("en", "English"),
                            ("pt-br", "Português (Brasil)"),
                            ("es", "Español"),
                            ("de", "Deutsch"),
                        ],
                        default="en",
                        max_length=10,
                    ),
                ),
                migrations.AlterField(
                    model_name="editionpipeline",
                    name="current_stage",
                    field=models.CharField(
                        choices=[
                            ("RAW", "Original (raw)"),
                            ("NORMALIZED", "Normalizado"),
                            ("SPLIT", "Split"),
                            ("CHUNKED", "Chunked"),
                            ("TRANSLATED", "Traduzido"),
                            ("REFINED", "Refine"),
                            ("MERGED", "Merge"),
                            ("POLISHED", "Polish (Codex)"),
                            ("MIOLO_MD", "Miolo MD"),
                            ("FINAL_MD", "MD Final"),
                            ("DONE", "Finalizado"),
                        ],
                        default="RAW",
                        max_length=20,
                    ),
                ),
                migrations.AlterField(
                    model_name="pipelineartifact",
                    name="stage",
                    field=models.CharField(
                        choices=[
                            ("raw", "RAW"),
                            ("normalize", "NORMALIZE"),
                            ("split", "SPLIT/CHUNK"),
                            ("translate", "TRANSLATE"),
                            ("refine", "REFINE"),
                            ("polish", "POLISH"),
                            ("miolo", "MIOLO"),
                            ("frontmatter", "FRONTMATTER"),
                            ("build", "BUILD"),
                            ("epub", "EPUB"),
                            ("pdf", "PDF"),
                            ("cover", "COVER"),
                        ],
                        db_index=True,
                        max_length=50,
                    ),
                ),
                migrations.DeleteModel(name="EditionBlock"),
            ],
        ),
    ]
