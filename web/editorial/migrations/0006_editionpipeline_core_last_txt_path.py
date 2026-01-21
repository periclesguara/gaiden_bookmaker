from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0005_edition_about_edition_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="editionpipeline",
            name="core_last_txt_path",
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
    ]
