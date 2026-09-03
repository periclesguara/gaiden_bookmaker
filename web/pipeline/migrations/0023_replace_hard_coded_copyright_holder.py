import re

from django.db import migrations


_HARD_CODED_HOLDER = re.compile(r"Arthur\s+Conan\s+Doy(?:l|k)e", re.IGNORECASE)


def replace_hard_coded_copyright_holder(apps, schema_editor):
    """Repair stored frontmatter without changing unrelated editorial fields."""
    targets = (
        ("pipeline", "BookEditionTemplate", "copyright_text"),
        ("editorial", "Edition", "copyright_template"),
    )

    for app_label, model_name, field_name in targets:
        model = apps.get_model(app_label, model_name)
        for record in model.objects.all().only("pk", field_name).iterator():
            previous = getattr(record, field_name) or ""
            repaired = _HARD_CODED_HOLDER.sub("{publisher}", previous)
            if repaired != previous:
                model.objects.filter(pk=record.pk).update(**{field_name: repaired})


class Migration(migrations.Migration):
    dependencies = [
        ("editorial", "0023_editionbuild_epubcheck_gate"),
        ("pipeline", "0022_alter_manualtranslationjob_status"),
    ]

    operations = [
        migrations.RunPython(
            replace_hard_coded_copyright_holder,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
