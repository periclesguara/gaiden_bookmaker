from __future__ import annotations

from pathlib import Path
from typing import Dict
import json
import re

from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string

from editorial.models import Edition, EditionBlock
from gaiden_portal.utils import country_for_language
from pipeline.services import paths as ppaths


def language_display(code: str) -> str:
    mapping = {
        "en": "English",
        "de": "Deutsch",
        "es": "Español",
        "ptbr": "Português",
        "pt-br": "Português",
        "fr": "Français",
        "it": "Italiano",
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
    extra_context: dict | None = None,
) -> str:
    language_code = lang_code or _edition_language_code(edition, "en")
    country_label = country_for_language(language_code, edition.country)
    context = {
        "edition": edition,
        "country_label": country_label,
        **(extra_context or {}),
    }
    if "text" not in context:
        block = (
            EditionBlock.objects.filter(edition=edition, block_type=module_name, is_locked=False)
            .only("text_md")
            .first()
        )
        if block and block.text_md:
            context["text"] = block.text_md
    for template_name in _frontmatter_template_candidates(module_name, language_code):
        try:
            return render_to_string(template_name, context).strip()
        except TemplateDoesNotExist:
            continue
    return ""


def render_frontmatter(edition: Edition) -> Dict[str, str]:
    fm: Dict[str, str] = {}
    language_code = _edition_language_code(edition, "en")
    illustrated = _is_illustrated_edition(edition)
    illustrated_notice = _illustrated_notice(language_code) if illustrated else ""
    blocks = {
        block.block_type: block
        for block in EditionBlock.objects.filter(edition=edition)
    }
    for name in [
        "frontispiece",
        "copyright",
        "about_edition",
        "introduction",
        "epilogue",
    ]:
        block = blocks.get(name)
        if not block or block.is_locked:
            continue
        text = (block.text_md or "").strip()
        if not text:
            continue
        if illustrated and name in ("frontispiece", "about_edition"):
            text = _normalize_illustrated_phrase(text, illustrated_notice, language_code)
            text = _append_illustrated_notice(text, illustrated_notice)
        rendered = render_frontmatter_module(
            edition,
            name,
            language_code,
            extra_context={"text": text},
        )
        if rendered:
            fm[name] = rendered.strip()
    return fm


def _illustrated_notice(lang_code: str) -> str:
    normalized = _normalize_lang_code(lang_code)
    mapping = {
        "en": "Illustrated edition.",
        "de": "Illustrierte Ausgabe · Modernes Deutsch",
        "es": "Edición ilustrada.",
        "pt_br": "Edição ilustrada.",
        "fr": "Édition illustrée",
        "it": "Edizione illustrata",
    }
    return mapping.get(normalized, "Illustrated edition.")


def _append_illustrated_notice(text: str, notice: str) -> str:
    if not notice:
        return text
    if notice.lower() in text.lower():
        return text
    return f"{text.rstrip()}\n\n{notice}"


def _normalize_illustrated_phrase(text: str, notice: str, lang_code: str) -> str:
    normalized = _normalize_lang_code(lang_code)
    if normalized != "de":
        return text
    if not notice:
        return text
    pattern = re.compile(
        r"Illustrierte Ausgabe(?:\s*[\(\[·\-–—]\s*Modernes Deutsch\s*[\)\]]?)?",
        flags=re.IGNORECASE,
    )
    return pattern.sub(notice, text)


def _is_illustrated_edition(edition: Edition) -> bool:
    book_code = getattr(getattr(edition, "work", None), "code", "")
    language_code = _edition_language_code(edition, "en")
    build_dir = ppaths.edition_build_dir_for_language(book_code, language_code)
    inserts_path = build_dir / "inserts.json"
    if inserts_path.exists():
        try:
            payload = json.loads(inserts_path.read_text(encoding="utf-8"))
            image_dir = payload.get("image_dir")
            if image_dir:
                return True
        except json.JSONDecodeError:
            return True
        return True

    images_dir = ppaths.data_dir() / "images" / book_code / language_code
    if images_dir.is_dir():
        for path in images_dir.rglob("*"):
            if path.is_file():
                return True
    return False


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
        "about_contributor",
        "introduction",
        "epilogue",
    ]
    fm = render_frontmatter(edition)
    for key in modules:
        out_path = out_dir / f"{key}.md"
        content = fm.get(key, "")
        if content.strip():
            out_path.write_text(content + "\n", encoding="utf-8")
        elif out_path.exists():
            out_path.unlink()


def build_merged_frontmatter(edition: Edition) -> str:
    fm = render_frontmatter(edition)
    order = [
        "frontispiece",
        "copyright",
        "about_edition",
        "introduction",
        "epilogue",
    ]
    merged = ""
    pagebreak = "\n\n<div style=\"page-break-after: always;\"></div>\n\n"
    first = True
    for key in order:
        content = fm.get(key, "").strip()
        if not content:
            continue
        if not first:
            merged += pagebreak
        merged += content.rstrip() + "\n"
        first = False
    return merged.rstrip() + "\n"
