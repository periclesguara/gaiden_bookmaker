from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0023_restore_preserved_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="work",
            name="source_provenance",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
