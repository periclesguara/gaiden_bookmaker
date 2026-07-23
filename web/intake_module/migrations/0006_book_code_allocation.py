from django.db import migrations, models
from django.db.models import Count


def audit_and_seed_sequence(apps, schema_editor):
    IntakeItem = apps.get_model("intake_module", "IntakeItem")
    BookCodeSequence = apps.get_model("intake_module", "BookCodeSequence")
    duplicates = list(
        IntakeItem.objects.exclude(book_code="")
        .values("book_code")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
        .order_by("book_code")
    )
    if duplicates:
        details = ", ".join(
            f"{row['book_code']} ({row['total']})" for row in duplicates
        )
        raise RuntimeError(
            "Duplicate IntakeItem book codes must be repaired before migration: " + details
        )
    BookCodeSequence.objects.get_or_create(
        name="book",
        defaults={"next_number": 33},
    )


class Migration(migrations.Migration):
    dependencies = [
        ("intake_module", "0005_translationjob_warning_confirmation"),
    ]

    operations = [
        migrations.AddField(
            model_name="intakebatch",
            name="book_code_plan_sha256",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="intakebatch",
            name="book_code_manifest",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="intakebatch",
            name="book_code_manifest_projected_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="intakebatch",
            name="book_code_manifest_projection_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="intakebatch",
            name="book_codes_allocated_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="intakebatch",
            name="book_codes_end",
            field=models.SlugField(blank=True),
        ),
        migrations.AddField(
            model_name="intakebatch",
            name="book_codes_reserved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="intakebatch",
            name="book_codes_reserved_by",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="intakebatch",
            name="book_codes_start",
            field=models.SlugField(blank=True),
        ),
        migrations.AddField(
            model_name="intakeitem",
            name="book_code_reserved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="intakeitem",
            name="book_code_reserved_by",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.CreateModel(
            name="BookCodeSequence",
            fields=[
                (
                    "name",
                    models.SlugField(
                        default="book",
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("next_number", models.PositiveIntegerField(default=33)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "intake_book_code_sequence"},
        ),
        migrations.RunPython(audit_and_seed_sequence, migrations.RunPython.noop),
    ]
