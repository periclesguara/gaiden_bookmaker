from __future__ import annotations

from pathlib import Path
from typing import Dict

from editorial.models import Edition


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


def render_frontmatter(edition: Edition) -> Dict[str, str]:
    ctx = build_context(edition)
    language = ctx.get("language") or "en"
    headings = {
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
    fm: Dict[str, str] = {}

    fm["frontispiece"] = (
        f"# {headings['frontispiece']}\n\n"
        + render_template(edition.frontispiece_template, ctx)
        + "\n\n::: pagebreak\n"
    )

    fm["copyright"] = (
        f"# {headings['copyright']}\n\n"
        + render_template(edition.copyright_template, ctx)
        + "\n\n::: pagebreak\n"
    )

    if edition.about_edition_template:
        fm["about_edition"] = (
            f"# {headings['about_edition']}\n\n"
            + render_template(edition.about_edition_template, ctx)
            + "\n\n::: pagebreak\n"
        )

    if edition.about_contributor_template:
        fm["about_contributor"] = (
            f"# {headings['about_contributor']}\n\n"
            + render_template(edition.about_contributor_template, ctx)
            + "\n\n::: pagebreak\n"
        )

    return fm


def build_frontmatter_files(edition: Edition, base_dir: Path) -> None:
    book_code = getattr(getattr(edition, "work", None), "code", "")
    language_code = getattr(edition, "language_code", None) or getattr(
        getattr(edition, "language", None), "code", ""
    )
    out_dir = base_dir / book_code / language_code
    out_dir.mkdir(parents=True, exist_ok=True)

    fm = render_frontmatter(edition)
    for key, value in fm.items():
        (out_dir / f"{key}.md").write_text(value, encoding="utf-8")


def build_merged_frontmatter(edition: Edition) -> str:
    fm = render_frontmatter(edition)
    order = ["frontispiece", "copyright", "about_edition", "about_contributor"]
    merged = ""
    for key in order:
        if key in fm:
            merged += fm[key].rstrip() + "\n\n"
    return merged.rstrip() + "\n"
