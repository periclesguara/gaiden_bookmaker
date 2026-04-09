from __future__ import annotations

from pathlib import Path

from . import edition_meta, paths
from .paths import data_dir, edition_build_dir


def _iter_existing_merges(edition, build_dir: Path) -> bool:
    """Return True if any canonical merge exists in the build dir."""
    return any((build_dir / name).exists() for name in paths.merge_priority_names(edition))


def sync_legacy_merges_from_translated(edition) -> None:
    """
    Backfill canonical merges (merge_translate/refine/polish.txt) from legacy
    files inside data/translated and data/normalized.

    Does not touch chunks.
    Does not overwrite existing canonical merges.
    """
    build_dir = edition_build_dir(edition)
    if _iter_existing_merges(edition, build_dir):
        return

    book_code = edition_meta.book_code(edition) or str(edition.id)

    candidate_dirs = [
        data_dir() / "translated" / book_code,
        data_dir() / "normalized" / book_code,
    ]

    mapping = {
        "translate": "merge_translate.txt",
        "refine": "merge_refine.txt",
        "polish": "merge_polish.txt",
    }

    build_dir.mkdir(parents=True, exist_ok=True)

    for base in candidate_dirs:
        if not base.exists():
            continue

        for path in base.rglob("merge*.txt"):
            lower_name = path.name.lower()
            for key, target_name in mapping.items():
                if key in lower_name:
                    target = build_dir / target_name
                    if not target.exists():
                        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
                    break

    translate_target = build_dir / "merge_translate.txt"
    if translate_target.exists():
        return

    target_language = edition_meta.language_code(edition)
    for variant_dir in paths.translated_variant_dirs(book_code, target_language):
        candidates = [
            variant_dir / f"merged_{variant_dir.name}.txt",
            variant_dir / "merged.txt",
        ]
        candidates.extend(sorted(variant_dir.glob("merged_*.txt")))
        for candidate in candidates:
            if not candidate.exists():
                continue
            translate_target.write_text(candidate.read_text(encoding="utf-8"), encoding="utf-8")
            return
