from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0021_pipelineartifact_sha256_alter_pipelineartifact_stage"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pipelineartifact",
            name="stage",
            field=models.CharField(
                choices=[
                    ("raw", "RAW"),
                    ("normalize", "NORMALIZE"),
                    ("structure_map", "STRUCTURE MAP"),
                    ("heading_clean", "HEADING CLEAN (LEGACY)"),
                    ("split", "SPLIT/CHUNK"),
                    ("translate", "TRANSLATE"),
                    ("translation_final", "TRANSLATION FINAL"),
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
    ]
