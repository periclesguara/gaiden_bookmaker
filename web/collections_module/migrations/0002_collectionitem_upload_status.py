from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("collections_module", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="collectionitem",
            name="upload_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("running", "Running"),
                    ("failed", "Failed"),
                    ("completed", "Completed"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
