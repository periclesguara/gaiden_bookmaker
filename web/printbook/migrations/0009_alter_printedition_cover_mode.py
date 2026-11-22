from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("printbook", "0008_printedition_cover_mode")]

    operations = [
        migrations.AlterField(
            model_name="printedition",
            name="cover_mode",
            field=models.CharField(
                choices=[
                    ("automatic_pdf", "Capa automática em PDF já configurada"),
                    ("new_print_cover", "Criar nova capa de impressão"),
                    ("adapt_epub_cover", "Adaptar a capa atual do EPUB"),
                    ("external_artwork", "Usar nova arte preparada externamente"),
                ],
                default="new_print_cover",
                max_length=30,
            ),
        ),
    ]
