from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("printbook", "0002_typography")]

    operations = [
        migrations.AddField(
            model_name="printedition",
            name="barcode_mode",
            field=models.CharField(
                choices=[("provider", "Gerado pela gráfica"), ("embedded", "Incorporado à arte")],
                default="provider",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="printedition",
            name="state",
            field=models.CharField(
                choices=[
                    ("DRAFT", "Rascunho"),
                    ("CONFIGURED", "Configurada"),
                    ("TYPESETTING", "Compondo"),
                    ("INTERIOR_READY", "Miolo pronto"),
                    ("COVER_PENDING", "Capa pendente"),
                    ("COVER_READY", "Capa pronta"),
                    ("PREFLIGHT_PENDING", "Verificação pendente"),
                    ("PRINT_READY", "Pronta para impressão"),
                    ("EXPORTED", "Exportada"),
                    ("INVALIDATED", "Invalidada"),
                ],
                default="CONFIGURED",
                max_length=30,
            ),
        ),
    ]
