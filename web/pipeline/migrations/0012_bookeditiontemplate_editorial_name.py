from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pipeline", "0011_alter_bookeditiontemplate_language"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookeditiontemplate",
            name="editorial_name",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]
