#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

from gaiden.raw_resolver import normalize_lang_fs

LANG_TOKENS = {
    "en",
    "es",
    "de",
    "fr",
    "it",
    "ptbr",
    "pt-br",
    "pt_br",
}

KEYWORD_PRIORITY = [
    "source",
    "raw",
    "input",
    "full",
    "original",
]


def _book_code_from_dir(name: str) -> str | None:
    match = re.match(r"^book_?(\d{1,4})", name, re.IGNORECASE)
    if not match:
        return None
    try:
        num = int(match.group(1))
    except ValueError:
        return None
    if num < 1 or num > 9999:
        return None
    return f"book_{num:04d}"


def _detect_lang(path: Path) -> str | None:
    parent = path.parent.name.lower()
    if parent in LANG_TOKENS:
        return normalize_lang_fs(parent)

    tokens = re.split(r"[^a-z0-9]+", path.stem.lower())
    for token in tokens:
        if token in LANG_TOKENS:
            return normalize_lang_fs(token)
    return None


def _priority_for(path: Path) -> int:
    name = path.name.lower()
    for idx, key in enumerate(KEYWORD_PRIORITY):
        if key in name:
            return idx
    return len(KEYWORD_PRIORITY) + 1


def _iter_candidate_files(book_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in book_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".txt", ".md"}:
            continue
        if re.search(r"(source|raw|input|full|original)", path.name, flags=re.IGNORECASE):
            candidates.append(path)
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description="Create canonical raw aliases from legacy inputs.")
    parser.add_argument("--data-dir", type=str, default="data")
    parser.add_argument("--dry-run", action="store_true", help="Only show actions; do not write files.")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    raw_root = data_dir / "raw"
    if not raw_root.is_dir():
        print(f"[FAIL] raw root not found: {raw_root}")
        return 2

    planned: dict[tuple[str, str], tuple[int, int, Path]] = {}
    for entry in sorted(raw_root.iterdir()):
        if not entry.is_dir():
            continue
        book_code = _book_code_from_dir(entry.name)
        if not book_code:
            continue
        for path in _iter_candidate_files(entry):
            lang = _detect_lang(path)
            if not lang:
                continue
            key = (book_code, lang)
            candidate = (_priority_for(path), len(path.parts), path)
            if key not in planned or candidate < planned[key]:
                planned[key] = candidate

    if not planned:
        print("[INFO] no legacy raw candidates found")
        return 0

    for (book_code, lang), (_prio, _depth, src) in sorted(planned.items()):
        dest_dir = data_dir / "raw" / book_code / lang
        dest_path = dest_dir / "source.txt"
        if dest_path.exists():
            print(f"[SKIP] exists: {dest_path}")
            continue
        action = f"COPY {src} -> {dest_path}"
        if args.dry_run:
            print(f"[DRY-RUN] {action}")
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_path)
        print(f"[OK] {action}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
