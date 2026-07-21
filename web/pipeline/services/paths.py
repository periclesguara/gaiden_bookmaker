from __future__ import annotations

from pathlib import Path

from . import utils
from . import edition_meta
from gaiden.infrastructure import storage


def _normalize_lang_token(value: str) -> str:
    return utils.normalize_lang(value)


def _project_root() -> Path:
    return storage.repo_root()


def data_dir() -> Path:
    return storage.data_dir()


def edition_build_dir(edition) -> Path:
    return storage.builds_dir(edition_meta.book_code(edition), edition_meta.language_code(edition))


def edition_build_dir_for_language(book_code: str, language: str) -> Path:
    return storage.builds_dir(book_code, language)


def translated_variant_dirs(book_code: str, language: str) -> list[Path]:
    translated_root = storage.translated_dir(book_code)
    if not translated_root.exists():
        return []

    target = _normalize_lang_token(language)
    matches: list[Path] = []
    for child in sorted(translated_root.iterdir()):
        if not child.is_dir():
            continue
        variant_lang = child.name.split("_", 1)[0]
        if _normalize_lang_token(variant_lang) == target:
            matches.append(child)
    return matches


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


def split_by_chapter_dir(edition) -> Path:
    return edition_build_dir(edition) / "split_by_chapter"


def split_refine_by_chapter_dir(edition) -> Path:
    return edition_build_dir(edition) / "split_refine_by_chapter"


def merge_refine_path(edition) -> Path:
    return edition_build_dir(edition) / "merge_refine.txt"


def merge_polish_path(edition) -> Path:
    return edition_build_dir(edition) / "merge_polish.txt"


def merge_polidor_path(edition) -> Path:
    return edition_build_dir(edition) / "merge_polidor.txt"


def core_last_txt_path(edition) -> Path:
    return storage.editions_dir(edition.id) / "core" / "core_last.txt"


def saved_core_reference_path(edition) -> Path | None:
    from editorial.models import EditionPipeline

    configured = (
        EditionPipeline.objects.filter(edition_id=edition.id)
        .values_list("core_last_txt_path", flat=True)
        .first()
        or ""
    )

    candidates: list[Path] = []
    if configured:
        configured_path = Path(configured)
        if configured_path.is_absolute():
            candidates.append(configured_path)
        else:
            candidates.append(storage.repo_root() / configured_path)
            candidates.append(storage.data_dir() / configured_path)
    candidates.append(core_last_txt_path(edition))

    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def saved_drive_return_reference_path(edition) -> Path | None:
    try:
        saved_from_drive = edition.text_snapshots.filter(
            stage="drive_return_reference"
        ).exists()
    except Exception:
        saved_from_drive = False
    if not saved_from_drive:
        return None
    return saved_core_reference_path(edition)


def pre_qa_md_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.PRE_QA.md"


def qa_md_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.QA.md"


def pre_edition_md_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.PRE_EDITION.md"


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


def refine_qa_json_path(edition) -> Path:
    return edition_build_dir(edition) / "REFINE.QA.json"


def refine_qa_md_path(edition) -> Path:
    return edition_build_dir(edition) / "REFINE.QA.md"


def preflight_json_path(edition) -> Path:
    return edition_build_dir(edition) / "PRE_FLIGHT.json"


def preflight_md_path(edition) -> Path:
    return edition_build_dir(edition) / "PRE_FLIGHT.md"


def final_md_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.MD_FINAL"


def build_md_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.BUILD.MD"


def epub_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.epub"


def pdf_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.PRINT.PDF"


def manifest_path(edition) -> Path:
    return edition_build_dir(edition) / "BOOK.MANIFEST.json"
