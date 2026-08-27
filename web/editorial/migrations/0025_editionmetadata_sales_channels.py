from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0024_editionmetadata"),
    ]

    operations = [
        migrations.AddField(
            model_name="editionmetadata",
            name="sales_channels",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "Lista de lojas adicionais. Cada item deve ter name, url e active. "
                    'Ex.: {"name":"IngramSpark","url":"https://...","active":true}.'
                ),
                verbose_name="Lojas / canais de venda",
            ),
        ),
    ]
