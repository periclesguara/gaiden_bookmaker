from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("intake_module", "0004_translationjob_and_identity_constraints"),
    ]

    operations = [
        migrations.AddField(
            model_name="translationjob",
            name="warning_confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="translationjob",
            name="warning_confirmed_by",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="translationjob",
            name="warning_confirmation_note",
            field=models.TextField(blank=True),
        ),
    ]
