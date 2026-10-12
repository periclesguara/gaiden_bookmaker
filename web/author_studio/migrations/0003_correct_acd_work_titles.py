import re
import unicodedata

from django.db import migrations
from django.utils.text import slugify


CORRECTIONS = {
    "ACD-HOUND": "The Hound of the Baskervilles",
    "ACD-SHER7": "The Case-Book of Sherlock Holmes",
}


def _canonicalize(value):
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(word.lower() for word in re.findall(r"[A-Za-z0-9]+", ascii_value))


def correct_titles(apps, schema_editor):
    Work = apps.get_model("author_studio", "Work")
    for code, title in CORRECTIONS.items():
        Work.objects.filter(code=code).update(
            title=title,
            canonical_title=_canonicalize(title),
            slug=slugify(title),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("author_studio", "0002_worksplit_workchunk"),
    ]

    operations = [
        migrations.RunPython(correct_titles, migrations.RunPython.noop),
    ]
