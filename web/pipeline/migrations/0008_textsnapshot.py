from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0001_initial"),
        ("pipeline", "0007_bookeditiontemplate_text_source_mode"),
    ]

    operations = [
        migrations.CreateModel(
            name="TextSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("language", models.CharField(max_length=10)),
                ("stage", models.CharField(max_length=50)),
                ("source_path", models.TextField(blank=True)),
                ("content", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "edition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="text_snapshots",
                        to="editorial.edition",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
