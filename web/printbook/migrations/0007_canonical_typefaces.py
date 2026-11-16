from django.db import migrations


TYPEFACES = (
    ("TeX Gyre Pagella", "TeX Gyre Pagella", "11.00", "14.00", "GUST Font License"),
    ("TeX Gyre Termes", "TeX Gyre Termes", "11.00", "14.00", "GUST Font License"),
    ("TeX Gyre Schola", "TeX Gyre Schola", "11.00", "14.00", "GUST Font License"),
    ("TeX Gyre Bonum", "TeX Gyre Bonum", "11.00", "14.00", "GUST Font License"),
    ("Noto Serif", "Noto Serif", "10.50", "14.00", "SIL Open Font License 1.1"),
)


def add_typefaces(apps, schema_editor):
    Profile = apps.get_model("printbook", "TypographyProfile")
    for name, font, size, leading, license_reference in TYPEFACES:
        Profile.objects.update_or_create(
            name=name,
            defaults={
                "font": font,
                "size_pt": size,
                "leading_pt": leading,
                "license_reference": license_reference,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("printbook", "0006_alter_printedition_trim_uiclap")]
    operations = [migrations.RunPython(add_typefaces, migrations.RunPython.noop)]
