from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import sys

from django.conf import settings

PROJECT_ROOT = Path(settings.BASE_DIR).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from gaiden.chapter_agent_split import write_chapter_split_artifacts

from . import paths


def _merge_translate_candidates(edition) -> list[Path]:
    build_dir = paths.edition_build_dir(edition)
    language = edition.language.code
    return [
        paths.merge_translate_path(edition),
        build_dir / f"merge_translate_{language}.txt",
    ]


def resolve_merge_translate_path(edition) -> Path:
    for candidate in _merge_translate_candidates(edition):
        if candidate.exists():
            return candidate
    raise FileNotFoundError("merge_translate.txt nao encontrado para split_by_chapter.")


def run_split_by_chapter(
    edition,
    *,
    parts_per_chapter: int = 1,
    max_chars_per_part: int | None = None,
) -> dict[str, Any]:
    merge_path = resolve_merge_translate_path(edition)
    split_root = paths.split_by_chapter_dir(edition)
    parts_dir = split_root / "parts"
    manifest_path = split_root / "manifest.json"

    if split_root.exists():
        shutil.rmtree(split_root)
    split_root.mkdir(parents=True, exist_ok=True)

    merged_text = merge_path.read_text(encoding="utf-8")
    manifest = write_chapter_split_artifacts(
        merged_text,
        parts_dir,
        manifest_path=manifest_path,
        parts_per_chapter=parts_per_chapter,
        max_chars_per_part=max_chars_per_part,
    )

    part_count = 0
    for chapter in manifest.get("chapters", []):
        part_count += len(chapter.get("parts") or [])

    return {
        "merge_translate_path": str(merge_path),
        "split_root": str(split_root),
        "parts_dir": str(parts_dir),
        "manifest_path": str(manifest_path),
        "chapter_count": int(manifest.get("chapter_count") or 0),
        "part_count": part_count,
        "max_chars_per_part": max_chars_per_part,
    }
