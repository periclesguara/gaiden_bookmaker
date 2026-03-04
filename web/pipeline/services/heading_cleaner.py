from __future__ import annotations

import json
import re
from pathlib import Path

from . import edition_meta, paths


def _parse_book_id(book_code: str) -> int | None:
    try:
        if book_code.startswith("book_"):
            return int(book_code.split("_", 1)[1])
    except (ValueError, IndexError):
        return None
    m = re.search(r"(\d+)", book_code)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _clean_chunk_text(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    prev_heading = ""
    blank_run = 0

    for line in lines:
        stripped = line.strip()
        is_heading = bool(re.match(r"^(chapter|capitulo)\b", stripped, flags=re.IGNORECASE))
        if is_heading and prev_heading and stripped.lower() == prev_heading.lower():
            continue
        if stripped == "":
            blank_run += 1
            if blank_run > 2:
                continue
        else:
            blank_run = 0
        if is_heading:
            prev_heading = stripped
        out.append(line)

    cleaned = "\n".join(out).strip()
    return f"{cleaned}\n" if cleaned else ""


def clean_path_for_book_code(book_code: str) -> Path:
    book_id = _parse_book_id(book_code)
    if book_id is None:
        return paths.data_dir() / "chunks" / book_code / "heading_cleaner" / "clean.txt"
    return paths.data_dir() / "chunks" / f"book_{book_id:04d}" / "heading_cleaner" / "clean.txt"


def run_heading_cleaner(edition, agent_name: str = "HeadingCleaner") -> dict[str, object]:
    book_code = edition_meta.book_code(edition)
    book_id = _parse_book_id(book_code)
    if book_id is None:
        raise ValueError("book_code must be like book_0001.")

    split_dir = paths.data_dir() / "chunks" / f"book_{book_id:04d}" / "split_01"
    chunk_paths = sorted(split_dir.glob("*.txt")) if split_dir.exists() else []
    if not chunk_paths:
        raise FileNotFoundError(f"Chunks not found for HeadingCleaner: {split_dir}")

    out_dir = paths.data_dir() / "chunks" / f"book_{book_id:04d}" / "heading_cleaner"
    out_dir.mkdir(parents=True, exist_ok=True)

    cleaned_items: list[dict[str, object]] = []
    merged_parts: list[str] = []
    for chunk_path in chunk_paths:
        original = chunk_path.read_text(encoding="utf-8")
        cleaned = _clean_chunk_text(original)
        out_path = out_dir / f"{chunk_path.stem}.clean.txt"
        out_path.write_text(cleaned, encoding="utf-8")
        cleaned_items.append(
            {
                "source": str(chunk_path),
                "output": str(out_path),
                "chars_in": len(original),
                "chars_out": len(cleaned),
            }
        )
        if cleaned.strip():
            merged_parts.append(cleaned.strip())

    clean_path = out_dir / "clean.txt"
    clean_path.write_text("\n\n".join(merged_parts).strip() + "\n", encoding="utf-8")

    report_path = out_dir / "heading_cleaner_report.json"
    report = {
        "schema": "heading_cleaner_v1",
        "agent_name": agent_name,
        "book_code": book_code,
        "source_dir": str(split_dir),
        "output_dir": str(out_dir),
        "clean_path": str(clean_path),
        "chunks": len(cleaned_items),
        "items": cleaned_items,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

    return {
        "agent_name": agent_name,
        "source_dir": str(split_dir),
        "output_dir": str(out_dir),
        "clean_path": str(clean_path),
        "chunks": len(cleaned_items),
        "report_path": str(report_path),
    }

