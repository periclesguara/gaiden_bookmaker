from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pipeline", "0012_bookeditiontemplate_editorial_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookeditiontemplate",
            name="edition_year",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="bookeditiontemplate",
            name="edition_copyright_holder",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]
