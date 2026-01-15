from __future__ import annotations

from pathlib import Path

from .paths import MERGE_PRIORITY, data_dir, edition_build_dir


def _iter_existing_merges(build_dir: Path) -> bool:
    """Return True if any canonical merge exists in the build dir."""
    return any((build_dir / name).exists() for name in MERGE_PRIORITY)


def sync_legacy_merges_from_translated(edition) -> None:
    """
    Backfill canonical merges (merge_translate/refine/polish.txt) from legacy
    files inside data/translated and data/normalized.

    Does not touch chunks.
    Does not overwrite existing canonical merges.
    """
    build_dir = edition_build_dir(edition)
    if _iter_existing_merges(build_dir):
        return

    book_code = getattr(edition, "book_code", str(edition.id))

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
