from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pipeline", "0009_bookeditiontemplate_frontmatter_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookeditiontemplate",
            name="city_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="bookeditiontemplate",
            name="country_name",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
