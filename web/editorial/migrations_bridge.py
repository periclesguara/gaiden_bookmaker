from __future__ import annotations

from pathlib import Path
from typing import Iterable

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

from editorial.models import Edition, Language, Seal, Work
from gaiden.infrastructure import storage

LANGUAGES: list[str] = ["en", "de", "es", "ptbr"]

LANGUAGE_VERBOSE = {
    "en": "English",
    "de": "Deutsch",
    "es": "Español",
    "ptbr": "Português (Brasil)",
}


def frontmatter_dir(book_code: str, language: str) -> Path:
    return storage.frontmatter_dir(book_code, language)


def read_file_if_exists(path: Path) -> str | None:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def strip_heading_and_pagebreak(text: str) -> str:
    lines = text.splitlines()

    if lines and lines[0].lstrip().startswith("#"):
        lines = lines[1:]
        if lines and lines[0].strip() == "":
            lines = lines[1:]

    while lines and lines[-1].strip() in (
        "\\newpage",
        "::: pagebreak",
        '<div style="page-break-before: always;"></div>',
    ):
        lines = lines[:-1]
        if lines and lines[-1].strip() == "":
            lines = lines[:-1]

    return "\n".join(lines).strip()


def _language_fk(language_code: str) -> Language:
    try:
        return Language.objects.get(code=language_code)
    except ObjectDoesNotExist:
        return Language.objects.create(
            code=language_code,
            name=LANGUAGE_VERBOSE.get(language_code, language_code),
            native_name=LANGUAGE_VERBOSE.get(language_code, language_code),
        )


def _seal_fk() -> Seal:
    try:
        return Seal.objects.get(name="MantaQuest")
    except ObjectDoesNotExist:
        return Seal.objects.create(slug="mantaquest", name="MantaQuest")


def get_or_create_edition(book_code: str, language_code: str) -> Edition:
    work = Work.objects.get(code=book_code)
    language = _language_fk(language_code)
    seal = _seal_fk()

    edition, created = Edition.objects.get_or_create(
        work=work,
        language=language,
        seal=seal,
        defaults={
            "publisher": "RinoBooks",
            "edition_year": work.year or 2026,
            "title": work.title,
            "author": work.author.name,
            "publication_year": 2026,
            "imprint_name": "RinoBooks",
            "seal_name": "MantaQuest",
            "language_code": language_code,
        },
    )

    if created and not edition.language_code:
        edition.language_code = language_code
        edition.save(update_fields=["language_code"])

    return edition


@transaction.atomic
def migrate_frontmatter_files_to_edition(book_code: str, language: str) -> Edition:
    edition = get_or_create_edition(book_code, language)
    base = frontmatter_dir(book_code, language)

    path_frontispiece = base / "frontispiece.md"
    path_copyright = base / "copyright.md"
    path_about_edition = base / "about_edition.md"
    path_about_contributor = base / "about_contributor.md"

    txt = read_file_if_exists(path_frontispiece)
    if txt:
        edition.frontispiece_template = strip_heading_and_pagebreak(txt)

    txt = read_file_if_exists(path_copyright)
    if txt:
        edition.copyright_template = strip_heading_and_pagebreak(txt)

    txt = read_file_if_exists(path_about_edition)
    if txt:
        edition.about_edition_template = strip_heading_and_pagebreak(txt)

    txt = read_file_if_exists(path_about_contributor)
    if txt:
        edition.about_contributor_template = strip_heading_and_pagebreak(txt)

    edition.save(
        update_fields=[
            "frontispiece_template",
            "copyright_template",
            "about_edition_template",
            "about_contributor_template",
        ]
    )
    return edition


def migrate_all_languages_for_book(book_code: str, languages: Iterable[str] = LANGUAGES):
    migrated: list[Edition] = []
    for lang in languages:
        base = frontmatter_dir(book_code, lang)
        if base.exists():
            migrated.append(migrate_frontmatter_files_to_edition(book_code, lang))
    return migrated
