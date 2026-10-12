from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0002_editionpipeline_translation_language"),
    ]

    operations = [
        migrations.AddField(
            model_name="edition",
            name="title",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="edition",
            name="subtitle",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="edition",
            name="author",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="edition",
            name="adapter",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="edition",
            name="about_edition_text",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="edition",
            name="publication_year",
            field=models.IntegerField(default=2026),
        ),
        migrations.AddField(
            model_name="edition",
            name="city",
            field=models.CharField(default="Rio de Janeiro", max_length=100),
        ),
        migrations.AddField(
            model_name="edition",
            name="country",
            field=models.CharField(
                choices=[("Brasil", "Brasil"), ("Brazil", "Brazil"), ("Brasilien", "Brasilien")],
                default="Brasil",
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name="edition",
            name="imprint_name",
            field=models.CharField(
                choices=[("RinoBooks", "RinoBooks")],
                default="RinoBooks",
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name="edition",
            name="seal_name",
            field=models.CharField(
                choices=[("MantaQuest", "MantaQuest")],
                default="MantaQuest",
                max_length=100,
            ),
        ),
        migrations.AddField(
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
    ]
