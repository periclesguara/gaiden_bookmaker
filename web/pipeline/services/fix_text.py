from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from editorial.models import Edition
from pipeline.services import canonical_index, utils

CHAPTER_RE = re.compile(
    r"^(?P<prefix>\s*#+\s*)(?P<label>chapter)\s+(?P<num>[0-9]+|[ivxlcdm]+)\s*[\.\:\-\)]*\s*(?P<title>.*)$",
    re.IGNORECASE,
)
PAGE_MARKER_RE = re.compile(r"^\s*PAGE\s+\d+\s*$", re.IGNORECASE)
NUMERIC_LINE_RE = re.compile(r"^\s*\d+\s*$")
BRACKET_NUMERIC_LINE_RE = re.compile(r"^\s*\[\s*\d+\s*\]\s*$")
ROMAN_LINE_RE = re.compile(r"^\s*[IVXLCDM]+\.?\s*$", re.IGNORECASE)
ROMAN_ONLY_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)
CHAPTER_STANDALONE_RE = re.compile(
    r"^\s*chapter\s+(?P<num>[0-9]+|[ivxlcdm]+)\s*[\.\:\-\)]*\s*(?P<title>.*)$",
    re.IGNORECASE,
)


class FixTextError(RuntimeError):
    """Raised when FIX_TEXT preconditions are not met."""


@dataclass
class HeadingItem:
    level: int
    key: str
    text: str
    line_no: int


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _utc_stamp() -> str:
    return canonical_index._utc_stamp()  # reuse same timestamp convention as canonical receipts


def _to_rel(path: Path) -> str:
    return canonical_index._to_rel(path)


def _git_text(args: list[str]) -> str:
    return canonical_index._git_text(args)


def _clean_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def roman_to_int(token: str) -> int | None:
    token = token.strip().upper().rstrip(".")
    if not token or not ROMAN_ONLY_RE.fullmatch(token):
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed(token):
        val = values[ch]
        if val < prev:
            total -= val
        else:
            total += val
            prev = val
    return total


def _parse_number(token: str) -> tuple[int | None, bool]:
    stripped = token.strip().rstrip(".")
    if stripped.isdigit():
        return int(stripped), False
    roman = roman_to_int(stripped)
    if roman is not None:
        return roman, True
    return None, False


def _normalize_title(text: str) -> str:
    value = text.strip()
    value = re.sub(r"^[\-\:\.\)\s]+", "", value)
    value = re.sub(r"\s+", " ", value)
    return value


def extract_heading_inventory(lines: list[str]) -> list[HeadingItem]:
    inventory: list[HeadingItem] = []
    for idx, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            continue
        hashes = len(stripped) - len(stripped.lstrip("#"))
        text = stripped[hashes:].strip()
        chapter = CHAPTER_RE.match(line)
        if chapter:
            title_key = _clean_key(_normalize_title(chapter.group("title")))
            key = f"chapter::{title_key}"
        else:
            key = f"heading::{_clean_key(text)}"
        inventory.append(HeadingItem(level=hashes, key=key, text=text, line_no=idx))
    return inventory


def compare_heading_inventory(before: list[HeadingItem], after: list[HeadingItem]) -> dict[str, Any]:
    before_sig = [(item.level, item.key) for item in before]
    after_sig = [(item.level, item.key) for item in after]
    if before_sig == after_sig:
        return {"ok": True, "reason": ""}
    reason = "heading_inventory_mismatch"
    if len(after_sig) < len(before_sig):
        reason = "heading_missing_after_fix"
    elif len(after_sig) > len(before_sig):
        reason = "heading_created_after_fix"
    return {
        "ok": False,
        "reason": reason,
        "before_count": len(before_sig),
        "after_count": len(after_sig),
        "before_signature": before_sig,
        "after_signature": after_sig,
    }


def _normalized_paths(edition: Edition) -> tuple[Path, Path, Path]:
    root = canonical_index.project_root()
    book_id = edition.work.code
    lang = utils.normalize_lang(edition.language.code)
    base_dir = root / "data" / "normalized" / book_id / lang
    normalized_v2 = base_dir / f"{book_id}_{lang}_v2.txt"
    normalized_md = base_dir / "normalized.md"
    fixed_md = base_dir / "normalized.fixed.md"
    return normalized_v2, normalized_md, fixed_md


def _resolve_normalized_md(edition: Edition) -> Path:
    normalized_v2, normalized_md, _fixed_md = _normalized_paths(edition)
    if normalized_md.exists():
        return normalized_md
    if not normalized_v2.exists():
        raise FixTextError(f"Normalized input not found: {normalized_md} (or {normalized_v2})")
    # Materialize normalized.md from normalize v2 output for a stable contract.
    _atomic_write_text(normalized_md, normalized_v2.read_text(encoding="utf-8"))
    return normalized_md


def latest_fix_report(book_id: str) -> dict[str, Any] | None:
    runs_root = canonical_index.project_root() / "docs" / "audit" / "runs"
    candidates = sorted(runs_root.glob(f"{book_id}_fix_text_*/fix_text_report.json"))
    if not candidates:
        return None
    path = candidates[-1]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    payload["report_path"] = _to_rel(path)
    payload["run_dir"] = _to_rel(path.parent)
    return payload


def fix_text(edition: Edition) -> dict[str, Any]:
    normalized_md = _resolve_normalized_md(edition)
    _normalized_v2, _normalized_md, fixed_md = _normalized_paths(edition)
    lines = normalized_md.read_text(encoding="utf-8").splitlines()

    headings_before = extract_heading_inventory(lines)
    actions = {
        "roman_conversions": 0,
        "removed_numeric_lines": 0,
        "removed_page_markers": 0,
        "suspect_orphans": 0,
    }
    converted_samples: list[dict[str, str]] = []
    removed_samples: list[str] = []

    output_lines: list[str] = []
    previous_kept_heading = False

    for line in lines:
        chapter_heading = CHAPTER_RE.match(line)
        if chapter_heading:
            prefix = chapter_heading.group("prefix")
            raw_num = chapter_heading.group("num")
            title = _normalize_title(chapter_heading.group("title"))
            parsed_num, converted_roman = _parse_number(raw_num)
            if parsed_num is None:
                normalized = f"{prefix}Chapter {raw_num}{' - ' + title if title else ''}"
            else:
                normalized = f"{prefix}Chapter {parsed_num:02d}{' - ' + title if title else ''}"
                if converted_roman:
                    actions["roman_conversions"] += 1
            if normalized != line and len(converted_samples) < 12:
                converted_samples.append({"from": line, "to": normalized})
            output_lines.append(normalized)
            previous_kept_heading = True
            continue

        raw = line.strip()
        # Remove high-confidence OCR/page noise.
        if PAGE_MARKER_RE.fullmatch(raw):
            actions["removed_page_markers"] += 1
            if len(removed_samples) < 12:
                removed_samples.append(line)
            previous_kept_heading = False
            continue
        if NUMERIC_LINE_RE.fullmatch(raw) or BRACKET_NUMERIC_LINE_RE.fullmatch(raw):
            actions["removed_numeric_lines"] += 1
            if len(removed_samples) < 12:
                removed_samples.append(line)
            previous_kept_heading = False
            continue
        if ROMAN_LINE_RE.fullmatch(raw):
            parsed_num = roman_to_int(raw)
            if parsed_num is not None and previous_kept_heading:
                normalized = str(parsed_num)
                if normalized != line and len(converted_samples) < 12:
                    converted_samples.append({"from": line, "to": normalized})
                actions["roman_conversions"] += 1
                output_lines.append(normalized)
            else:
                actions["suspect_orphans"] += 1
                if len(removed_samples) < 12:
                    removed_samples.append(line)
            previous_kept_heading = False
            continue

        standalone = CHAPTER_STANDALONE_RE.match(line)
        if standalone:
            num_token = standalone.group("num")
            title = _normalize_title(standalone.group("title"))
            number, converted_roman = _parse_number(num_token)
            if number is None:
                generated = f"# Chapter {num_token}{' - ' + title if title else ''}"
            else:
                generated = f"# Chapter {number:02d}{' - ' + title if title else ''}"
                if converted_roman:
                    actions["roman_conversions"] += 1
            if len(converted_samples) < 12:
                converted_samples.append({"from": line, "to": generated})
            output_lines.append(generated)
            previous_kept_heading = True
            continue

        output_lines.append(line)
        previous_kept_heading = line.lstrip().startswith("#")

    headings_after = extract_heading_inventory(output_lines)
    heading_diff = compare_heading_inventory(headings_before, headings_after)

    book_id = edition.work.code
    run_dir = canonical_index.project_root() / "docs" / "audit" / "runs" / f"{book_id}_fix_text_{_utc_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=True)

    status = "PASS" if heading_diff["ok"] else "FAIL"
    if status == "PASS":
        out_text = "\n".join(output_lines).rstrip("\n") + "\n"
        _atomic_write_text(fixed_md, out_text)

    report: dict[str, Any] = {
        "status": status,
        "input_path": _to_rel(normalized_md),
        "output_path": _to_rel(fixed_md),
        "headings_before": [item.text for item in headings_before],
        "headings_after": [item.text for item in headings_after],
        "heading_diff": heading_diff,
        "actions": actions,
        "samples": {
            "converted": converted_samples,
            "removed": removed_samples,
        },
        "run_dir": _to_rel(run_dir),
    }
    if status == "PASS":
        report["input_sha256"] = canonical_index.sha256_file(normalized_md)
        report["output_sha256"] = canonical_index.sha256_file(fixed_md)

    manifest = {
        "book_id": book_id,
        "lang": utils.normalize_lang(edition.language.code),
        "input_path": report["input_path"],
        "output_path": report["output_path"],
        "status": status,
    }
    _atomic_write_text(run_dir / "fix_text_report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    _atomic_write_text(run_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    _atomic_write_text(run_dir / "git_head.txt", _git_text(["git", "rev-parse", "HEAD"]) + "\n")
    _atomic_write_text(run_dir / "git_status.txt", _git_text(["git", "status", "-sb"]) + "\n")
    return report
