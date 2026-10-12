from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pipeline", "0006_alter_bookeditiontemplate_collaborator_roles"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookeditiontemplate",
            name="text_source_mode",
            field=models.CharField(default="auto", max_length=100),
        ),
    ]
