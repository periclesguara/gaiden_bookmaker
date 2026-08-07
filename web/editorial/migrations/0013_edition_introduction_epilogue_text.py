from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0012_pipelineartifact"),
    ]

    operations = [
        migrations.AddField(
            model_name="edition",
            name="introduction_text",
            field=models.TextField(blank=True, default="", null=True),
        ),
        migrations.AddField(
            model_name="edition",
            name="epilogue_text",
            field=models.TextField(blank=True, default="", null=True),
        ),
    ]
