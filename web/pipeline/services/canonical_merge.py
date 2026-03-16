from __future__ import annotations

import re
from pathlib import Path

from gaiden.chunk_contract import HeadingMatch, detect_heading
from gaiden.translate import sanitize_generated_chunk_text


def _iter_non_merged_txt_files(directory: Path | None) -> list[Path]:
    if not directory or not directory.exists():
        return []
    return sorted(
        p for p in directory.glob("*.txt")
        if not (p.name == "merged.txt" or p.name.startswith("merged_") or p.name.startswith("merge_"))
    )


def _normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _heading_match(text: str) -> HeadingMatch | None:
    lines = _normalize_text(text).splitlines()
    if not lines:
        return None
    return detect_heading(lines, 0)


def _strip_leading_heading(text: str, match: HeadingMatch) -> str:
    lines = _normalize_text(text).splitlines()
    body = "\n".join(lines[match.consumed_lines :]).strip()
    return body


def _normalize_editorial_punctuation(text: str) -> str:
    normalized = _normalize_text(text)
    normalized = re.sub(r"(?<=\w)'(?=\w)", "’", normalized)
    normalized = re.sub(r'(^|[\s(\[{—–])"', r"\1“", normalized)
    normalized = normalized.replace('"', "”")
    return normalized


def canonicalize_chunk_text(source_text: str, candidate_text: str) -> str:
    source_clean = _normalize_text(source_text)
    candidate_clean = _normalize_text(candidate_text)

    try:
        candidate_clean = sanitize_generated_chunk_text(candidate_clean)
    except RuntimeError:
        candidate_clean = _normalize_text(candidate_clean)

    source_heading = _heading_match(source_clean)
    if source_heading is None:
        return _normalize_editorial_punctuation(candidate_clean)

    candidate_heading = _heading_match(candidate_clean)
    body = candidate_clean
    if candidate_heading is not None:
        body = _strip_leading_heading(candidate_clean, candidate_heading)

    body = _normalize_editorial_punctuation(body)
    heading_line = source_heading.heading_line.strip()
    if not body:
        return heading_line
    return f"{heading_line}\n\n{body}"


def canonicalize_chunk_dir_in_place(source_dir: Path | None, candidate_dir: Path | None) -> dict[str, int]:
    changed = 0
    total = 0
    for candidate_path in _iter_non_merged_txt_files(candidate_dir):
        total += 1
        source_text = ""
        if source_dir:
            source_path = source_dir / candidate_path.name
            if source_path.exists():
                source_text = source_path.read_text(encoding="utf-8")
        candidate_text = candidate_path.read_text(encoding="utf-8")
        canonical = canonicalize_chunk_text(source_text, candidate_text).rstrip() + "\n"
        if candidate_text != canonical:
            candidate_path.write_text(canonical, encoding="utf-8")
            changed += 1
    return {"total": total, "changed": changed}


def build_canonical_merged_text(candidate_dir: Path | None) -> str:
    parts: list[str] = []
    for candidate_path in _iter_non_merged_txt_files(candidate_dir):
        text = candidate_path.read_text(encoding="utf-8").rstrip()
        if text:
            parts.append(text)
    if not parts:
        return ""
    return "\n\n".join(parts).rstrip() + "\n"


def write_canonical_merge(
    source_dir: Path | None,
    candidate_dir: Path | None,
    out_path: Path,
) -> tuple[Path, dict[str, int]]:
    stats = canonicalize_chunk_dir_in_place(source_dir, candidate_dir)
    merged = build_canonical_merged_text(candidate_dir)
    if not merged:
        raise FileNotFoundError(f"No canonical chunk outputs found in {candidate_dir}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(merged, encoding="utf-8")
    return out_path, stats
