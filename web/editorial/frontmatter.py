from __future__ import annotations

from pathlib import Path
from typing import Dict

from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string

from editorial.models import Edition
from gaiden_portal.utils import country_for_language


def language_display(code: str) -> str:
    mapping = {
        "en": "English",
        "de": "Deutsch",
        "es": "Español",
        "ptbr": "Português",
        "pt-br": "Português",
    }
    return mapping.get(code, code)

def _edition_language_code(edition: Edition, fallback: str = "en") -> str:
    related_lang = getattr(getattr(edition, "language", None), "code", "")
    if related_lang:
        return related_lang
    legacy_code = getattr(edition, "language_code", "") or ""
    return legacy_code or fallback


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


def _normalize_lang_code(code: str) -> str:
    normalized = (code or "en").lower().replace("-", "_")
    if normalized == "ptbr":
        return "pt_br"
    return normalized


def _frontmatter_template_candidates(module: str, lang_code: str) -> list[str]:
    normalized = _normalize_lang_code(lang_code)
    return [
        f"gaiden/{module}_{normalized}.md.j2",
        f"pipeline/{module}_{normalized}.md.j2",
    ]


def render_frontmatter_module(
    edition: Edition,
    module_name: str,
    lang_code: str | None = None,
) -> str:
    language_code = lang_code or _edition_language_code(edition, "en")
    country_label = country_for_language(language_code, edition.country)
    context = {
        "edition": edition,
        "country_label": country_label,
    }
    for template_name in _frontmatter_template_candidates(module_name, language_code):
        try:
            return render_to_string(template_name, context).strip()
        except TemplateDoesNotExist:
            continue
    return ""


def render_frontmatter(edition: Edition) -> Dict[str, str]:
    fm: Dict[str, str] = {}
    language_code = _edition_language_code(edition, "en")
    for name in [
        "frontispiece",
        "copyright",
        "about_edition",
        "introduction",
        "epilogue",
        "about_contributor",
    ]:
        rendered = render_frontmatter_module(edition, name, language_code)
        if rendered:
            fm[name] = rendered + "\n\n::: pagebreak\n"
    return fm


def build_frontmatter_files(edition: Edition, base_dir: Path) -> None:
    book_code = getattr(getattr(edition, "work", None), "code", "")
    language_code = getattr(edition, "language_code", None) or getattr(
        getattr(edition, "language", None), "code", ""
    )
    out_dir = base_dir / book_code / language_code
    out_dir.mkdir(parents=True, exist_ok=True)

    modules = [
        "frontispiece",
        "copyright",
        "about_edition",
        "introduction",
        "epilogue",
        "about_contributor",
    ]
    for key in modules:
        rendered = render_frontmatter_module(edition, key, language_code)
        content = f"{rendered}\n\n::: pagebreak\n" if rendered else ""
        (out_dir / f"{key}.md").write_text(content, encoding="utf-8")


def build_merged_frontmatter(edition: Edition) -> str:
    fm = render_frontmatter(edition)
    order = [
        "frontispiece",
        "copyright",
        "about_edition",
        "introduction",
        "epilogue",
        "about_contributor",
    ]
    merged = ""
    for key in order:
        if key in fm:
            merged += fm[key].rstrip() + "\n\n"
    return merged.rstrip() + "\n"
