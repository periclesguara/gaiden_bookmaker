from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("editorial", "0012_pipelineartifact"),
    ]

    operations = [
        migrations.AddField(
            model_name="pipelineartifact",
            name="status",
            field=models.CharField(default="OK", max_length=16),
        ),
        migrations.AddField(
            model_name="pipelineartifact",
            name="sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
