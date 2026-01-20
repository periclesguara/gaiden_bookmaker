from __future__ import annotations

from django.template.loader import render_to_string

from gaiden_portal.utils import country_for_language
from . import paths


def _language_code(edition) -> str:
    lang = getattr(edition, "language_code", None)
    if not lang:
        lang_obj = getattr(edition, "language", None)
        lang = getattr(lang_obj, "code", None) or getattr(edition, "language", None)
    if not lang:
        return "en"
    if lang == "ptbr":
        return "pt-br"
    return lang


def _language_label(lang: str) -> str:
    return {
        "en": "English",
        "pt-br": "Português",
        "es": "Español",
        "de": "Deutsch",
    }.get(lang, lang.upper())


def _frontmatter_template(name: str, lang: str) -> str:
    suffix = {
        "pt-br": "pt_br",
        "en": "en",
        "es": "es",
        "de": "de",
    }.get(lang, "en")
    return f"pipeline/{name}_{suffix}.md.j2"


def run_build(edition) -> dict:
    final_md = paths.final_md_path(edition)
    if not final_md.exists():
        raise FileNotFoundError(f"MD final not found: {final_md}")

    md_text = final_md.read_text(encoding="utf-8")

    lang = _language_code(edition)
    context = {
        "edition": edition,
        "language_label": _language_label(lang),
        "country_label": country_for_language(lang, getattr(edition, "country", "")),
    }
    front = render_to_string(_frontmatter_template("frontispiece", lang), context)
    copyright_page = render_to_string(_frontmatter_template("copyright", lang), context)
    about_edition = render_to_string(_frontmatter_template("about_edition", lang), context)
    about_contrib = render_to_string("pipeline/about_contributor.md.j2", {"edition": edition})

    parts = [
        front.strip(),
        "",
        copyright_page.strip(),
        "",
        about_edition.strip(),
        "",
        about_contrib.strip(),
        "",
        md_text.strip(),
        "",
    ]
    build_text = "\n".join(parts)

    out_path = paths.build_md_path(edition)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_text, encoding="utf-8")

    return {
        "path": str(out_path),
        "preview": build_text[:2000],
    }
