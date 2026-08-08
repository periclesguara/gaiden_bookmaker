from django.db import migrations, models

import writer.language_contract


_LANGUAGE_ALIASES = {
    "en-us": "en-US",
    "en_us": "en-US",
    "english": "en-US",
    "inglês": "en-US",
    "ingles": "en-US",
    "american english": "en-US",
    "en-gb": "en-GB",
    "en-uk": "en-GB",
    "en_gb": "en-GB",
    "en_uk": "en-GB",
    "british english": "en-GB",
    "pt-br": "pt-BR",
    "pt_br": "pt-BR",
    "ptbr": "pt-BR",
    "português (brasil)": "pt-BR",
    "portugues (brasil)": "pt-BR",
    "português brasileiro": "pt-BR",
    "portugues brasileiro": "pt-BR",
}


def reconcile_existing_project_languages(apps, schema_editor):
    StoryProject = apps.get_model("writer", "StoryProject")
    for project in StoryProject.objects.all().only("id", "language").iterator():
        raw_language = (project.language or "").strip()
        canonical = _LANGUAGE_ALIASES.get(raw_language.casefold())
        if canonical is None:
            raise RuntimeError(
                "Writer project "
                f"{project.pk} has unsupported legacy language {raw_language!r}; "
                "reconcile it explicitly to en-US, en-GB or pt-BR before migration."
            )
        StoryProject.objects.filter(pk=project.pk).update(
            language=canonical,
            language_contract=writer.language_contract.language_contract_for(canonical),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("writer", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="storyproject",
            name="language",
            field=models.CharField(
                choices=[
                    ("en-US", "EN-US — Inglês americano"),
                    ("en-GB", "EN-UK — Inglês britânico"),
                    ("pt-BR", "PT-BR — Português brasileiro"),
                ],
                default="en-US",
                max_length=40,
            ),
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
        migrations.RunPython(
            reconcile_existing_project_languages,
            migrations.RunPython.noop,
        ),
    ]
