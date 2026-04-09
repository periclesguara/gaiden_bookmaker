from __future__ import annotations

from pathlib import Path
from typing import Dict
import re

from editorial.models import Edition
from pipeline.models import BookEditionTemplate, ensure_bookeditiontemplate_runtime_columns

FIXED_FRONTMATTER_ORDER = ["frontispiece", "copyright"]
FRONT_BLOCK_ORDER = ["frontispiece", "copyright", "about_edition", "about_contributor", "preface", "introduction"]
BACK_BLOCK_ORDER = ["epilogue"]
ALL_SECTION_FILES = FRONT_BLOCK_ORDER + BACK_BLOCK_ORDER
BLANK_MARKERS = {"blank", "[blank]", "{blank}", "__blank__"}


def language_display(code: str) -> str:
    mapping = {
        "en": "English",
        "de": "Deutsch",
        "es": "Español",
        "fr": "Français",
        "it": "Italiano",
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
            "about_edition": "About This Book",
            "preface": "Preface",
            "introduction": "Introduction",
            "epilogue": "Epilogue",
            "about_contributor": "About the Author",
        },
        "de": {
            "frontispiece": "Frontispiz",
            "copyright": "Impressum",
            "about_edition": "Über dieses Buch",
            "preface": "Vorwort",
            "introduction": "Einführung",
            "epilogue": "Epilog",
            "about_contributor": "Über den Autor",
        },
        "es": {
            "frontispiece": "Frontispicio",
            "copyright": "Copyright",
            "about_edition": "Sobre este libro",
            "preface": "Prefacio",
            "introduction": "Introducción",
            "epilogue": "Epílogo",
            "about_contributor": "Sobre el autor",
        },
        "it": {
            "frontispiece": "Frontespizio",
            "copyright": "Copyright",
            "about_edition": "Su questo libro",
            "preface": "Prefazione",
            "introduction": "Introduzione",
            "epilogue": "Epilogo",
            "about_contributor": "Sull'autore",
        },
        "ptbr": {
            "frontispiece": "Frontispício",
            "copyright": "Copyright",
            "about_edition": "Sobre este livro",
            "preface": "Prefácio",
            "introduction": "Introdução",
            "epilogue": "Epílogo",
            "about_contributor": "Sobre o autor",
        },
        "pt-br": {
            "frontispiece": "Frontispício",
            "copyright": "Copyright",
            "about_edition": "Sobre este livro",
            "preface": "Prefácio",
            "introduction": "Introdução",
            "epilogue": "Epílogo",
            "about_contributor": "Sobre o autor",
        },
    }.get(language, {
        "frontispiece": "Frontispiece",
        "copyright": "Copyright",
        "about_edition": "About This Book",
        "preface": "Preface",
        "introduction": "Introduction",
        "epilogue": "Epilogue",
        "about_contributor": "About the Author",
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


def _normalize_frontispiece_body(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    blocks = [line for line in lines if line]
    return "\n\n".join(blocks).strip()


def _normalize_copyright_body(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return text

    preface: list[str] = []
    rest: list[str] = []
    in_rest = False

    for raw in lines:
        stripped = raw.strip()
        if not in_rest and stripped == "---":
            in_rest = True
            rest.append(raw)
            continue
        if not in_rest:
            if stripped:
                preface.append(stripped)
            continue
        rest.append(raw.rstrip())

    credit_block = "\n\n".join(preface).strip()
    trailing = "\n".join(rest).strip()
    if credit_block and trailing:
        return f"{credit_block}\n\n{trailing}".strip()
    return credit_block or trailing


def _normalize_frontmatter_body(section_key: str, text: str) -> str:
    if section_key == "frontispiece":
        return _normalize_frontispiece_body(text)
    if section_key == "copyright":
        return _normalize_copyright_body(text)
    return text


def _section_file_name(section_name: str) -> str:
    if section_name == "about_edition":
        return "about_this_book.md"
    return f"{section_name}.md"


def _resolve_template(edition: Edition) -> BookEditionTemplate | None:
    book_code = getattr(getattr(edition, "work", None), "code", "")
    language_code = getattr(edition, "language_code", None) or getattr(
        getattr(edition, "language", None), "code", ""
    )
    if not book_code or not language_code:
        return None
    ensure_bookeditiontemplate_runtime_columns()
    return BookEditionTemplate.objects.filter(book_code=book_code, language=language_code).first()


def _optional_section_specs(template: BookEditionTemplate | None) -> list[tuple[str, bool, str]]:
    if template is None:
        return [
            ("preface", False, ""),
            ("introduction", False, ""),
            ("epilogue", False, ""),
        ]
    return [
        ("preface", bool(template.has_preface), template.preface_rendered),
        ("introduction", bool(template.has_introduction), template.introduction_rendered),
        ("epilogue", bool(template.has_epilogue), template.epilogue_rendered),
    ]


def optional_section_warnings(template: BookEditionTemplate | None, language: str) -> list[str]:
    headings = frontmatter_headings(language)
    warnings: list[str] = []
    for key, enabled, body in _optional_section_specs(template):
        if enabled and not sanitize_section_body(body, headings[key]):
            warnings.append(f"{headings[key]} marcado, mas sem conteudo. Bloco sera ignorado na montagem final.")
    return warnings


def build_frontmatter_sections(language: str, rendered_sections: dict[str, str]) -> Dict[str, str]:
    headings = frontmatter_headings(language)
    fm: Dict[str, str] = {}
    for key in FRONT_BLOCK_ORDER + BACK_BLOCK_ORDER:
        body = sanitize_section_body(rendered_sections.get(key, ""), headings[key])
        if not body:
            continue
        body = _normalize_frontmatter_body(key, body)
        fm[key] = f"# {headings[key]}\n\n{body}\n\n::: pagebreak\n"
    return fm


def merge_frontmatter_sections(sections: Dict[str, str]) -> str:
    merged = ""
    for key in FRONT_BLOCK_ORDER:
        if key in sections:
            merged += sections[key].rstrip() + "\n\n"
    return merged.rstrip() + "\n"


def render_frontmatter(edition: Edition) -> Dict[str, str]:
    ctx = build_context(edition)
    language = ctx.get("language") or "en"
    template = _resolve_template(edition)
    rendered_sections = {
        "frontispiece": render_template(edition.frontispiece_template, ctx),
        "copyright": render_template(edition.copyright_template, ctx),
        "about_edition": render_template(edition.about_edition_template, ctx),
        "about_contributor": render_template(edition.about_contributor_template, ctx),
    }
    for key, enabled, body in _optional_section_specs(template):
        rendered_sections[key] = body if enabled else ""
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
    for key in ALL_SECTION_FILES:
        (out_dir / _section_file_name(key)).write_text(fm.get(key, ""), encoding="utf-8")
    # Legacy alias kept to avoid breaking older tooling that still reads about_edition.md.
    (out_dir / "about_edition.md").write_text(fm.get("about_edition", ""), encoding="utf-8")


def build_merged_frontmatter(edition: Edition) -> str:
    return merge_frontmatter_sections(render_frontmatter(edition))
