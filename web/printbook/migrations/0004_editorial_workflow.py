from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("printbook", "0003_printedition_barcode_mode_and_states")]

    operations = [
        migrations.AlterField(
            model_name="printedition",
            name="platform",
            field=models.CharField(
                choices=[
                    ("ingramspark", "IngramSpark"),
                    ("amazon_kdp", "Amazon KDP"),
                    ("uiclap", "UICLAP"),
                ],
                default="ingramspark",
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="printedition",
            name="binding",
            field=models.CharField(
                choices=[
                    ("paperback", "Paperback"),
                    ("hardcover", "Hardcover"),
                    ("hardcover_dust_jacket", "Hardcover com sobrecapa"),
                ],
                default="paperback",
                max_length=30,
            ),
        ),
        migrations.AddField(model_name="printedition", name="composition_settings", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="printedition", name="cover_settings", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="printedition", name="configuration_history", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="printedition", name="interior_approval", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="printedition", name="cover_approval", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="printedition", name="visual_review_approved_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="printedition", name="edition_approved_at", field=models.DateTimeField(blank=True, null=True)),
    ]
