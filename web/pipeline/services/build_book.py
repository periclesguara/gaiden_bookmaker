from __future__ import annotations

from editorial.frontmatter import build_merged_frontmatter
from . import edition_meta, paths


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


def run_build(edition, language_override: str | None = None) -> dict:
    book_code = edition_meta.book_code(edition)
    build_dir = (
        paths.edition_build_dir_for_language(book_code, language_override)
        if language_override
        else paths.edition_build_dir(edition)
    )
    final_md = build_dir / "BOOK.MD_FINAL"
    if not final_md.exists():
        raise FileNotFoundError(f"MD final not found: {final_md}")

    md_text = final_md.read_text(encoding="utf-8")

    frontmatter = build_merged_frontmatter(edition).strip()
    if frontmatter:
        build_text = f"{frontmatter}\n\n{md_text.strip()}\n"
    else:
        build_text = md_text.strip() + "\n"

    out_path = build_dir / "BOOK.BUILD.MD"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_text, encoding="utf-8")

    return {
        "path": str(out_path),
        "preview": build_text[:2000],
    }
