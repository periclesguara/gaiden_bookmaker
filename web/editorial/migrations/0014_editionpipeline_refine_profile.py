from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("editorial", "0013_alter_edition_copyright_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="editionpipeline",
            name="refine_profile",
            field=models.CharField(blank=True, default="ingles_neutro", max_length=30),
        ),
    ]
