from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("editorial", "0015_merge_20260127_1950"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pipelineartifact",
            name="stage",
            field=models.CharField(
                choices=[
                    ("raw", "RAW"),
                    ("normalize", "NORMALIZE"),
                    ("split", "SPLIT/CHUNK"),
                    ("translate", "TRANSLATE"),
                    ("merge_translate", "MERGE_TRANSLATE"),
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
