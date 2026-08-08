from django.db import migrations, models

import writer.language_contract


class Migration(migrations.Migration):
    dependencies = [
        ("writer", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="storyproject",
            name="language",
            field=models.CharField(default="pt-BR", max_length=40),
        ),
        migrations.AddField(
            model_name="storyproject",
            name="language_contract",
            field=models.JSONField(
                default=writer.language_contract.default_language_contract,
                validators=[writer.language_contract.validate_language_contract],
            ),
        ),
        migrations.AddField(
            model_name="chaptersession",
            name="language_contract",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="chaptersession",
            name="language_contract_sha256",
            field=models.CharField(blank=True, default="", max_length=64),
            preserve_default=False,
        ),
    ]
