from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0004_alter_edition_country"),
    ]

    operations = [
        migrations.AddField(
            model_name="edition",
            name="about_edition_text",
            field=models.TextField(blank=True),
        ),
    ]
