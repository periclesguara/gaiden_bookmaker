from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("intake_module", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="intakeitem",
            name="handoff_edition_id",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="intakeitem",
            name="handoff_raw_path",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="intakeitem",
            name="handoff_raw_sha256",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="intakeitem",
            name="handoff_translated_path",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="intakeitem",
            name="handoff_translated_sha256",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="intakeitem",
            name="handed_off_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
