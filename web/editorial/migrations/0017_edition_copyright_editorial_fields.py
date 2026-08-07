from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("editorial", "0016_pipelineartifact_merge_translate_stage"),
    ]

    operations = [
        migrations.AddField(
            model_name="edition",
            name="copyright_text",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="edition",
            name="editorial_name",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]
