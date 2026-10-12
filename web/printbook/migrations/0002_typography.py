from django.db import migrations


def seed_profiles(apps, schema_editor):
    Profile = apps.get_model("printbook", "TypographyProfile")
    for name, size, leading in [("Romance", 11, 14), ("Clássico", 11, 15), ("Filosofia", 11, 15), ("Acadêmico", 10, 13), ("Técnico", 10, 13)]:
        Profile.objects.get_or_create(name=name, defaults={"font": "TeX Gyre Pagella", "size_pt": size, "leading_pt": leading})


class Migration(migrations.Migration):
    dependencies = [("printbook", "0001_initial")]
    operations = [migrations.RunPython(seed_profiles, migrations.RunPython.noop)]
