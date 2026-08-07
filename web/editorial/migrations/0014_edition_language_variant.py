from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0013_edition_introduction_epilogue_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="edition",
            name="language_variant",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
