from __future__ import annotations

from pathlib import Path
from typing import Dict
import re

from editorial.models import Edition

SECTION_ORDER = ["frontispiece", "copyright", "about_edition", "about_contributor"]
BLANK_MARKERS = {"blank", "[blank]", "{blank}", "__blank__"}


def language_display(code: str) -> str:
    mapping = {
        "en": "English",
        "de": "Deutsch",
        "es": "Español",
        "ptbr": "Português",
        "pt-br": "Português",
    }
    return mapping.get(code, code)


def build_context(edition: Edition) -> dict:
    language_code = getattr(edition, "language_code", None) or getattr(
        getattr(edition, "language", None), "code", ""
    )
    imprint = edition.imprint_name or edition.seal_name
    publisher = edition.publisher or edition.imprint_name or ""
    title = edition.title or getattr(getattr(edition, "work", None), "title", "")
    subtitle = edition.subtitle or ""
    author = edition.author or getattr(getattr(edition, "work", None), "author", None)
    if hasattr(author, "name"):
        author = author.name

    return {
        "book_code": getattr(getattr(edition, "work", None), "code", ""),
        "language": language_code,
        "imprint": imprint,
        "title": title,
        "subtitle": subtitle,
        "author": author or "",
        "adapter": edition.adapter or "",
        "translator": edition.translator or "",
        "editor": edition.editor or "",
        "year": edition.publication_year,
        "city": edition.city,
        "country": edition.country,
        "publisher": publisher,
        "language_display": language_display(language_code),
    }


def render_template(tpl: str, ctx: dict) -> str:
    try:
        return tpl.format(**ctx)
    except KeyError as exc:
        return f"[MISSING {exc}] {tpl}"


def frontmatter_headings(language: str) -> dict[str, str]:
    return {
        "en": {
            "frontispiece": "Frontispiece",
            "copyright": "Copyright",
            "about_edition": "About this Edition",
            "about_contributor": "About the Contributors",
        },
        "de": {
            "frontispiece": "Frontispiz",
            "copyright": "Copyright",
            "about_edition": "Über diese Ausgabe",
            "about_contributor": "Über die Mitwirkenden",
        },
        "es": {
            "frontispiece": "Frontispicio",
            "copyright": "Copyright",
            "about_edition": "Sobre esta edición",
            "about_contributor": "Sobre los colaboradores",
        },
        "ptbr": {
            "frontispiece": "Frontispício",
            "copyright": "Copyright",
            "about_edition": "Sobre esta edição",
            "about_contributor": "Sobre os colaboradores",
        },
        "pt-br": {
            "frontispiece": "Frontispício",
            "copyright": "Copyright",
            "about_edition": "Sobre esta edição",
            "about_contributor": "Sobre os colaboradores",
        },
    }.get(language, {
        "frontispiece": "Frontispiece",
        "copyright": "Copyright",
        "about_edition": "About this Edition",
        "about_contributor": "About the Contributors",
    })


def _normalize_heading(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" :.-_#*`").lower())


def _is_blank_marker(value: str) -> bool:
    return value.strip().lower() in BLANK_MARKERS


def _strip_leading_duplicate_heading(text: str, section_heading: str) -> str:
    if not text.strip():
        return ""

    lines = text.splitlines()
    target = _normalize_heading(section_heading)
    index = 0

    while index < len(lines) and not lines[index].strip():
        index += 1

    # Drop repeated heading lines at the top (e.g., "# Copyright", "## Copyright", "**Copyright**").
    while index < len(lines):
        line = lines[index].strip()
        heading_match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if heading_match:
            candidate = heading_match.group(1)
        elif line.startswith("**") and line.endswith("**") and len(line) > 4:
            candidate = line[2:-2]
        else:
            candidate = line

        if _normalize_heading(candidate) != target:
            break

        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1

    return "\n".join(lines[index:]).strip()


def sanitize_section_body(text: str, section_heading: str) -> str:
    stripped = (text or "").strip()
    if not stripped or _is_blank_marker(stripped):
        return ""
    return _strip_leading_duplicate_heading(stripped, section_heading).strip()


def build_frontmatter_sections(language: str, rendered_sections: dict[str, str]) -> Dict[str, str]:
    headings = frontmatter_headings(language)
    fm: Dict[str, str] = {}
    for key in SECTION_ORDER:
        body = sanitize_section_body(rendered_sections.get(key, ""), headings[key])
        if not body:
            continue
        fm[key] = f"# {headings[key]}\n\n{body}\n\n::: pagebreak\n"
    return fm


def merge_frontmatter_sections(sections: Dict[str, str]) -> str:
    merged = ""
    for key in SECTION_ORDER:
        if key in sections:
            merged += sections[key].rstrip() + "\n\n"
    return merged.rstrip() + "\n"


def render_frontmatter(edition: Edition) -> Dict[str, str]:
    ctx = build_context(edition)
    language = ctx.get("language") or "en"
    rendered_sections = {
        "frontispiece": render_template(edition.frontispiece_template, ctx),
        "copyright": render_template(edition.copyright_template, ctx),
        "about_edition": render_template(edition.about_edition_template, ctx),
        "about_contributor": render_template(edition.about_contributor_template, ctx),
    }
    return build_frontmatter_sections(language, rendered_sections)


def build_frontmatter_files(edition: Edition, base_dir: Path) -> None:
    book_code = getattr(getattr(edition, "work", None), "code", "")
    language_code = getattr(edition, "language_code", None) or getattr(
        getattr(edition, "language", None), "code", ""
    )
    out_dir = base_dir / book_code / language_code
    out_dir.mkdir(parents=True, exist_ok=True)

    fm = render_frontmatter(edition)
    # Blank-safe save: missing section is persisted as empty file to prevent stale leftovers.
    for key in SECTION_ORDER:
        (out_dir / f"{key}.md").write_text(fm.get(key, ""), encoding="utf-8")


def build_merged_frontmatter(edition: Edition) -> str:
    return merge_frontmatter_sections(render_frontmatter(edition))
