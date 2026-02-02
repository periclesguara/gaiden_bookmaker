from __future__ import annotations

from pathlib import Path
import os
import re

from django.conf import settings

from . import edition_meta


def _project_root() -> Path:
    return Path(settings.BASE_DIR).parent


def data_dir() -> Path:
    return _project_root() / "data"


def edition_build_dir(edition) -> Path:
    return data_dir() / "builds" / edition_meta.book_code(edition) / edition_meta.language_code(edition)


def edition_build_dir_for_language(book_code: str, language: str) -> Path:
    return data_dir() / "builds" / book_code / language


MERGE_PRIORITY = ["merge_refine.txt", "merge_polish.txt", "merge_translate.txt"]
FORCE_MERGE_TRANSLATE_MARKER = "FORCE_MERGE_TRANSLATE"
LEGACY_MERGE_NAMES = [
    "MERGE_POLISH.TXT",
    "MERGE_REFINE.TXT",
    "MERGE_TRANSLATE.TXT",
]

def merge_priority_names_for_language(language: str, build_dir: Path | None = None) -> list[str]:
    if language == "es":
        return list(MERGE_PRIORITY)
    if language == "de":
        if build_dir and (build_dir / FORCE_MERGE_TRANSLATE_MARKER).exists():
            return ["merge_translate.txt", "merge_refine.txt"]
        return ["merge_refine.txt", "merge_translate.txt"]
    if build_dir and (build_dir / FORCE_MERGE_TRANSLATE_MARKER).exists():
        return ["merge_translate.txt", "merge_refine.txt", "merge_polish.txt"]
    return list(MERGE_PRIORITY)


def merge_priority_names(edition) -> list[str]:
    build_dir = edition_build_dir(edition)
    edition_lang = getattr(getattr(edition, "language", None), "code", "") or getattr(
        edition, "language_code", ""
    )
    return merge_priority_names_for_language(edition_lang, build_dir)


def merge_paths(edition) -> list[Path]:
    build_dir = edition_build_dir(edition)
    return [build_dir / name for name in merge_priority_names(edition)]


def _legacy_target_name(legacy_name: str) -> str | None:
    upper = legacy_name.upper()
    if "POLISH" in upper:
        return "merge_polish.txt"
    if "REFINE" in upper:
        return "merge_refine.txt"
    if "TRANSLATE" in upper:
        return "merge_translate.txt"
    return None


def sync_legacy_merges(edition) -> None:
    build_dir = edition_build_dir(edition)
    if any((build_dir / name).exists() for name in MERGE_PRIORITY):
        return
    for legacy_name in LEGACY_MERGE_NAMES:
        legacy_path = build_dir / legacy_name
        if not legacy_path.exists():
            continue
        target_name = _legacy_target_name(legacy_name)
        if not target_name:
            continue
        target_path = build_dir / target_name
        if target_path.exists():
            return
        target_path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
        return


def final_merge_txt_path(edition) -> Path | None:
    sync_legacy_merges(edition)
    for path in merge_paths(edition):
        if path.exists():
            return path
    return None


def merge_translate_path(edition) -> Path:
    return edition_build_dir(edition) / "merge_translate.txt"


def merge_refine_path(edition) -> Path:
    return edition_build_dir(edition) / "merge_refine.txt"


def merge_polish_path(edition) -> Path:
    return edition_build_dir(edition) / "merge_polish.txt"


def core_last_txt_path(edition) -> Path:
    return data_dir() / "editions" / str(edition.id) / "core" / "core_last.txt"


MD_VERSION_DEFAULT = "v01"
MD_VERSION_RE = re.compile(r"^v?(\d+)$", re.IGNORECASE)


def _normalize_version(value: str | None) -> str:
    if not value:
        return MD_VERSION_DEFAULT
    raw = value.strip().lower()
    m = MD_VERSION_RE.match(raw)
    if not m:
        digits = re.search(r"\d+", raw)
        if not digits:
            return MD_VERSION_DEFAULT
        num = int(digits.group(0))
    else:
        num = int(m.group(1))
    if num < 0:
        num = 0
    return f"v{num:02d}"


def _discover_latest_version(build_dir: Path, language: str) -> str | None:
    if not build_dir.exists():
        return None
    pattern = re.compile(
        rf"^book\.{re.escape(language)}\.v(\d+)(?:\.|$)",
        re.IGNORECASE,
    )
    best = None
    for path in build_dir.glob(f"book.{language}.v*.md"):
        m = pattern.match(path.name)
        if not m:
            continue
        num = int(m.group(1))
        if best is None or num > best:
            best = num
    if best is None:
        return None
    return f"v{best:02d}"


def md_version(
    edition=None,
    language: str | None = None,
    override: str | None = None,
    build_dir: Path | None = None,
) -> str:
    if override:
        return _normalize_version(override)
    env_value = os.environ.get("GAIDEN_MD_VERSION", "").strip()
    if env_value:
        return _normalize_version(env_value)
    if build_dir and language:
        discovered = _discover_latest_version(build_dir, language)
        if discovered:
            return _normalize_version(discovered)
    return MD_VERSION_DEFAULT


def book_md_basename(language: str, version: str) -> str:
    return f"book.{language}.{version}"


def pre_qa_md_path(edition, language: str | None = None, version: str | None = None) -> Path:
    lang = language or edition_meta.language_code(edition)
    build_dir = edition_build_dir_for_language(edition_meta.book_code(edition), lang)
    ver = md_version(edition, lang, override=version, build_dir=build_dir)
    return build_dir / f"{book_md_basename(lang, ver)}.pre_qa.md"


def qa_md_path(edition, language: str | None = None, version: str | None = None) -> Path:
    lang = language or edition_meta.language_code(edition)
    build_dir = edition_build_dir_for_language(edition_meta.book_code(edition), lang)
    ver = md_version(edition, lang, override=version, build_dir=build_dir)
    return build_dir / f"{book_md_basename(lang, ver)}.qa.md"


def pre_edition_md_path(edition, language: str | None = None, version: str | None = None) -> Path:
    lang = language or edition_meta.language_code(edition)
    build_dir = edition_build_dir_for_language(edition_meta.book_code(edition), lang)
    ver = md_version(edition, lang, override=version, build_dir=build_dir)
    return build_dir / f"{book_md_basename(lang, ver)}.pre_edition.md"


MIOL_TERM_VERSION = "v1"


def miolo_md_filename(version: str | None = None) -> str:
    return f"MIOL_TERM.{version or MIOL_TERM_VERSION}.md"


def miolo_md_path(edition, version: str | None = None) -> Path:
    return edition_build_dir(edition) / miolo_md_filename(version)


def miolo_md_path_for_language(
    book_code: str, language: str, version: str | None = None
) -> Path:
    return edition_build_dir_for_language(book_code, language) / miolo_md_filename(version)


def qa_log_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.QA_LOG.json"


def final_md_path(edition, language: str | None = None, version: str | None = None) -> Path:
    lang = language or edition_meta.language_code(edition)
    build_dir = edition_build_dir_for_language(edition_meta.book_code(edition), lang)
    ver = md_version(edition, lang, override=version, build_dir=build_dir)
    return build_dir / f"{book_md_basename(lang, ver)}.md"


def build_md_path(edition, language: str | None = None, version: str | None = None) -> Path:
    lang = language or edition_meta.language_code(edition)
    build_dir = edition_build_dir_for_language(edition_meta.book_code(edition), lang)
    ver = md_version(edition, lang, override=version, build_dir=build_dir)
    return build_dir / f"{book_md_basename(lang, ver)}.build.md"


def kdp_merged_md_path(edition, language: str | None = None, version: str | None = None) -> Path:
    lang = language or edition_meta.language_code(edition)
    build_dir = edition_build_dir_for_language(edition_meta.book_code(edition), lang)
    ver = md_version(edition, lang, override=version, build_dir=build_dir)
    return build_dir / f"{book_md_basename(lang, ver)}.kdp_merged.md"


def canonical_ready_md_path(edition, language: str | None = None, version: str | None = None) -> Path:
    lang = language or edition_meta.language_code(edition)
    ver = md_version(edition, lang, override=version)
    return (
        data_dir()
        / "canonical"
        / edition_meta.book_code(edition)
        / lang
        / f"{book_md_basename(lang, ver)}.ready.md"
    )


def epub_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.epub"


def pdf_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.PRINT.PDF"


def manifest_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.MANIFEST.json"
