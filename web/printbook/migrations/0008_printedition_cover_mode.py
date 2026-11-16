from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("printbook", "0007_canonical_typefaces")]

    operations = [
        migrations.AddField(
            model_name="printedition",
            name="cover_mode",
            field=models.CharField(
                choices=[
                    ("new_print_cover", "Criar nova capa de impressão"),
                    ("adapt_epub_cover", "Adaptar a capa atual do EPUB"),
                    ("external_artwork", "Usar nova arte preparada externamente"),
                ],
                default="new_print_cover",
                max_length=30,
            ),
        ),
    ]
