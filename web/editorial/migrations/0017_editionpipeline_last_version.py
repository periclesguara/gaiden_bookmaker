from pathlib import Path

from django.db import migrations, models


def seed_last_version_fields(apps, schema_editor):
    EditionPipeline = apps.get_model("editorial", "EditionPipeline")
    EditionBuild = apps.get_model("editorial", "EditionBuild")
    for state in EditionPipeline.objects.select_related("edition", "edition__language"):
        latest = (
            EditionBuild.objects.filter(
                edition=state.edition,
                language_code=state.edition.language.code,
            )
            .exclude(epub_path="")
            .order_by("-build_version", "-created_at")
            .first()
        )
        if latest and latest.epub_path:
            state.last_version_path = latest.epub_path
            state.last_version_filename = Path(latest.epub_path).name
            state.save(update_fields=["last_version_path", "last_version_filename"])


class Migration(migrations.Migration):

    dependencies = [
        ("editorial", "0016_editionpipeline_build_outdated_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="editionpipeline",
            name="last_version_filename",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="editionpipeline",
            name="last_version_path",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.RunPython(seed_last_version_fields, migrations.RunPython.noop),
    ]
