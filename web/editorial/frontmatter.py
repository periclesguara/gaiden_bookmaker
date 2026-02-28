from __future__ import annotations

from pathlib import Path
from typing import Dict

from editorial.models import Edition
<<<<<<< Updated upstream
=======
from gaiden_portal.utils import country_for_language
from pipeline.models import BookEditionTemplate


DEFAULT_PUBLISHER = "Péricles Guará Silva"
DEFAULT_IMPRINT = "MantaQuest"
>>>>>>> Stashed changes


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

    year_value = edition.edition_year or edition.publication_year
    ctx = {
        "book_code": getattr(getattr(edition, "work", None), "code", ""),
        "language": language_code,
        "imprint": imprint,
        "title": title,
        "subtitle": subtitle,
        "author": author or "",
        "adapter": edition.adapter or "",
        "translator": edition.translator or "",
        "editor": edition.editor or "",
        "year": year_value,
        "city": edition.city,
        "country": edition.country,
        "publisher": publisher,
        "language_display": language_display(language_code),
    }

    work_code = getattr(getattr(edition, "work", None), "code", "")
    try:
        tmpl = BookEditionTemplate.objects.filter(
            book_code=work_code,
            language=language_code,
        ).first()
    except Exception:
        tmpl = None

    if tmpl:
        imprint_val = (
            getattr(tmpl, "imprint_name", None)
            or getattr(tmpl, "imprint", None)
            or getattr(tmpl, "seal", None)
            or getattr(tmpl, "seal_name", None)
        )
        publisher_val = (
            getattr(tmpl, "publisher", None)
            or getattr(tmpl, "publisher_name", None)
            or getattr(tmpl, "editorial", None)
            or getattr(tmpl, "editorial_name", None)
        )

        if imprint_val:
            ctx["imprint"] = imprint_val
        if publisher_val:
            ctx["publisher"] = publisher_val

    ctx["imprint"] = (ctx.get("imprint") or DEFAULT_IMPRINT).strip()
    ctx["publisher"] = (ctx.get("publisher") or DEFAULT_PUBLISHER).strip()

    return ctx


def render_template(tpl: str, ctx: dict) -> str:
    try:
        return tpl.format(**ctx)
    except KeyError as exc:
        return f"[MISSING {exc}] {tpl}"


<<<<<<< Updated upstream
=======
def _normalize_lang_code(code: str) -> str:
    normalized = (code or "en").lower().replace("-", "_")
    if normalized == "ptbr":
        return "pt_br"
    return normalized


def _frontmatter_template_candidates(module: str, lang_code: str) -> list[str]:
    normalized = _normalize_lang_code(lang_code)
    candidates = [
        f"gaiden/{module}_{normalized}.md.j2",
        f"gaiden/{module}.md.j2",
        f"pipeline/{module}_{normalized}.md.j2",
        f"pipeline/{module}.md.j2",
    ]
    if normalized != "en":
        candidates.append(f"gaiden/{module}_en.md.j2")
        candidates.append(f"pipeline/{module}_en.md.j2")
    return candidates


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
        **build_context(edition),
    }
    for template_name in _frontmatter_template_candidates(module_name, language_code):
        try:
            return render_to_string(template_name, context).strip()
        except TemplateDoesNotExist:
            continue
    return ""


>>>>>>> Stashed changes
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
    modules = [
        "frontispiece",
        "copyright",
        "about_edition",
        "introduction",
        "epilogue",
        "about_contributor",
    ]
    for key in modules:
        out_path = out_dir / f"{key}.md"
        content = fm.get(key, "")
        if content.strip():
            out_path.write_text(content, encoding="utf-8")
        elif out_path.exists():
            out_path.unlink()


def build_merged_frontmatter(edition: Edition) -> str:
    fm = render_frontmatter(edition)
    order = ["frontispiece", "copyright", "about_edition", "about_contributor"]
    merged = ""
    for key in order:
        if key in fm:
            merged += fm[key].rstrip() + "\n\n"
    return merged.rstrip() + "\n"
