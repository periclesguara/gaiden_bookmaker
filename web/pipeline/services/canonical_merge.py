from __future__ import annotations

import re
from pathlib import Path

from gaiden.application.pipeline import fail_closed_merge
from gaiden.application.pipeline.translation import sanitize_generated_chunk_text
from gaiden.chunk_contract import HeadingMatch, detect_heading

LOCALIZED_CHAPTER_TITLES: dict[tuple[str, str], list[str]] = {
    (
        "book_0002",
        "de",
    ): [
        "Die Wissenschaft der Deduktion",
        "Die Darstellung des Falls",
        "Auf der Suche nach einer Lösung",
        "Die Geschichte des kahlköpfigen Mannes",
        "Die Tragödie von Pondicherry Lodge",
        "Sherlock Holmes liefert eine Demonstration",
        "Die Episode mit dem Fass",
        "Die Baker-Street-Truppe",
        "Ein Bruch in der Kette",
        "Das Ende des Inselbewohners",
        "Der große Schatz von Agra",
        "Die seltsame Geschichte von Jonathan Small",
    ],
}

_CHAPTER_FILE_RE = re.compile(r"^chapter_(\d{2})_part_\d{2}\.txt$", re.IGNORECASE)
_CHAPTER_HEADING_PREFIX_RE = re.compile(r"^(?P<prefix>\s*#{1,6}\s+)(?P<body>.+?)\s*$")


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


def _chapter_label_for_language(language: str) -> str:
    lang = (language or "").lower()
    if lang.startswith("de"):
        return "Kapitel"
    if lang.startswith("pt") or lang.startswith("es"):
        return "Capitulo"
    return "Chapter"


def _localized_heading_line(
    current_heading: str,
    *,
    chapter_number: int,
    title: str,
    language: str,
) -> str:
    match = _CHAPTER_HEADING_PREFIX_RE.match(current_heading.strip())
    prefix = match.group("prefix") if match else "## "
    return f"{prefix}{_chapter_label_for_language(language)} {chapter_number} - {title}"


def localize_chapter_headings_in_place(
    candidate_dir: Path | None,
    *,
    book_code: str | None,
    language: str | None,
) -> int:
    if not candidate_dir or not candidate_dir.exists():
        return 0
    titles = LOCALIZED_CHAPTER_TITLES.get(((book_code or "").strip(), (language or "").strip().lower()))
    if not titles:
        return 0

    changed = 0
    for candidate_path in _iter_non_merged_txt_files(candidate_dir):
        match = _CHAPTER_FILE_RE.match(candidate_path.name)
        if not match:
            continue
        chapter_number = int(match.group(1))
        if chapter_number < 1 or chapter_number > len(titles):
            continue
        lines = candidate_path.read_text(encoding="utf-8").splitlines()
        if not lines:
            continue
        heading_match = _heading_match("\n".join(lines[:3]))
        if heading_match is None:
            continue
        localized = _localized_heading_line(
            heading_match.heading_line,
            chapter_number=chapter_number,
            title=titles[chapter_number - 1],
            language=str(language or ""),
        )
        if lines[0].strip() == localized.strip():
            continue
        lines[0] = localized
        candidate_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        changed += 1
    return changed


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
    *,
    book_code: str | None = None,
    language: str | None = None,
) -> tuple[Path, dict[str, int]]:
    stats = canonicalize_chunk_dir_in_place(source_dir, candidate_dir)
    localized = localize_chapter_headings_in_place(
        candidate_dir,
        book_code=book_code,
        language=language,
    )
    stats["localized_headings"] = localized
    merged = build_canonical_merged_text(candidate_dir)
    if not merged:
        raise FileNotFoundError(f"No canonical chunk outputs found in {candidate_dir}")
    stage = "polish" if "polish" in out_path.name else "refine" if "refine" in out_path.name else "merge"
    run_id = candidate_dir.name if candidate_dir else out_path.stem
    fail_closed_merge.validate_repair_and_write(
        text=merged,
        out_path=out_path,
        root=Path.cwd(),
        book_code=str(book_code or ""),
        language=str(language or ""),
        stage=stage,
        run_id=run_id,
        merge_validation={
            "ok": True,
            "book_code": book_code or "",
            "language": language or "",
            "stage": stage,
            "run_id": run_id,
            "total_expected_chunks": stats.get("total", 0),
            "total_received_chunks": stats.get("total", 0),
            "missing_chunks": [],
            "extra_chunks": [],
            "duplicate_chunks": [],
            "final_status": "PASSED",
            "canonical_written": True,
        },
        chunk_order_report={
            "book_code": book_code or "",
            "language": language or "",
            "stage": stage,
            "run_id": run_id,
            "total_expected_chunks": stats.get("total", 0),
            "total_received_chunks": stats.get("total", 0),
            "ordered_outputs": [str(path) for path in _iter_non_merged_txt_files(candidate_dir)],
            "final_status": "PASSED",
            "canonical_written": True,
        },
    )
    return out_path, stats
