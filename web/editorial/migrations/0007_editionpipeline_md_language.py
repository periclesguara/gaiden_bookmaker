from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0006_editionpipeline_core_last_txt_path"),
    ]

    operations = [
        migrations.AddField(
            model_name="editionpipeline",
            name="md_language",
            field=models.CharField(blank=True, max_length=10, null=True),
        ),
    ]
