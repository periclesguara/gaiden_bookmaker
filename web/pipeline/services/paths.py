from __future__ import annotations

from pathlib import Path

from django.conf import settings


def _project_root() -> Path:
    return Path(settings.BASE_DIR).parent


def data_dir() -> Path:
    return _project_root() / "data"


def edition_build_dir(edition) -> Path:
    return data_dir() / "builds" / edition.book_code / edition.language


MERGE_PRIORITY = ["merge_polish.txt", "merge_refine.txt", "merge_translate.txt"]
FORCE_MERGE_TRANSLATE_MARKER = "FORCE_MERGE_TRANSLATE"
LEGACY_MERGE_NAMES = [
    "MERGE_POLISH.TXT",
    "MERGE_REFINE.TXT",
    "MERGE_TRANSLATE.TXT",
]


def merge_priority_names(edition) -> list[str]:
    build_dir = edition_build_dir(edition)
    if (build_dir / FORCE_MERGE_TRANSLATE_MARKER).exists():
        return ["merge_translate.txt", "merge_refine.txt", "merge_polish.txt"]
    return list(MERGE_PRIORITY)


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


def pre_qa_md_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.PRE_QA.md"


def qa_md_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.QA.md"


def pre_edition_md_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.PRE_EDITION.md"


def qa_log_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.QA_LOG.json"


def final_md_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.MD_FINAL"


def build_md_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.BUILD.MD"


def epub_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.EPUB3"


def pdf_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.PRINT.PDF"


def manifest_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.MANIFEST.json"
