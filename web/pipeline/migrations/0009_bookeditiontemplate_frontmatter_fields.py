from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pipeline", "0008_textsnapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookeditiontemplate",
            name="seal_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="bookeditiontemplate",
            name="editor_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="bookeditiontemplate",
            name="translator_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="bookeditiontemplate",
            name="adapter_name",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
