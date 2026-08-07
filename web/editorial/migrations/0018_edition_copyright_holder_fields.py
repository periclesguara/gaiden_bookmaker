from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("editorial", "0017_edition_copyright_editorial_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="edition",
            name="edition_copyright_holder",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]
