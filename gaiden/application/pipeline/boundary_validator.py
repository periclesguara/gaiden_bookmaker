from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .safe_text_splitter import ends_with_pending_connector, has_terminal_punctuation


CONTINUATION_START_RE = re.compile(
    r"^(having|to|live|just\s+as|as|because|since|while|when|if|and|but|or|nor|which|who|whose|that)\b",
    re.IGNORECASE,
)
CONNECTOR_DUPLICATION_RE = re.compile(r"\b(for\s+just\s+as|just\s+as|as)\s*$", re.IGNORECASE)
HEADING_RE = re.compile(r"^(?:#{1,6}\s*)?(BOOK|CHAPTER|Book|Chapter)\s+([IVXLCDM]+|\d+)\.?$", re.IGNORECASE)
QUOTE_FRAGMENT_RE = re.compile(r"^[“\"].*[,;:]”?$")


@dataclass(frozen=True)
class BoundaryError:
    type: str
    line: int
    previous: str
    next: str


@dataclass(frozen=True)
class AutoRepair:
    type: str
    line: int
    previous: str
    next: str
    repaired: str


def _paragraphs_with_lines(text: str) -> list[tuple[int, str]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    paragraphs: list[tuple[int, str]] = []
    current: list[str] = []
    start_line = 1
    for index, line in enumerate(lines, start=1):
        if line.strip():
            if not current:
                start_line = index
            current.append(line)
            continue
        if current:
            paragraphs.append((start_line, "\n".join(current).strip()))
            current = []
    if current:
        paragraphs.append((start_line, "\n".join(current).strip()))
    return paragraphs


def _join_fragments(previous: str, next_text: str) -> str:
    nxt = next_text.strip()
    if re.search(r"\bmay\s*$", previous.strip(), re.IGNORECASE):
        nxt = re.sub(r"^to\s+", "", nxt, count=1, flags=re.IGNORECASE)
    if nxt:
        nxt = nxt[0].lower() + nxt[1:]
    return f"{previous.rstrip()} {nxt}".strip()


def _is_heading(text: str) -> bool:
    return bool(HEADING_RE.match(text.strip()))


def _is_mechanical_incomplete(previous: str, next_text: str) -> bool:
    if _is_heading(previous) or _is_heading(next_text):
        return False
    if QUOTE_FRAGMENT_RE.match(previous.strip()) and re.match(r"^(and|but)\b", next_text.strip(), re.IGNORECASE):
        return False
    if has_terminal_punctuation(previous):
        return False
    if ends_with_pending_connector(previous):
        return True
    return bool(CONTINUATION_START_RE.match(next_text.strip()))


def _is_connector_duplication(previous: str, next_text: str) -> bool:
    if _is_heading(previous) or _is_heading(next_text):
        return False
    prev = previous.strip()
    nxt = next_text.strip()
    if not (CONNECTOR_DUPLICATION_RE.search(prev) and re.match(r"^just\s+as\b", nxt, re.IGNORECASE)):
        return False
    return True


def validate_boundaries(text: str) -> dict[str, Any]:
    errors: list[BoundaryError] = []
    paragraphs = _paragraphs_with_lines(text)
    for idx in range(len(paragraphs) - 1):
        line, previous = paragraphs[idx]
        _next_line, next_text = paragraphs[idx + 1]
        if _is_connector_duplication(previous, next_text):
            errors.append(
                BoundaryError(
                    type="BOUNDARY_DUPLICATION_ERROR",
                    line=line,
                    previous=previous,
                    next=next_text,
                )
            )
            continue
        if _is_mechanical_incomplete(previous, next_text):
            errors.append(
                BoundaryError(
                    type="INCOMPLETE_SENTENCE_BOUNDARY_ERROR",
                    line=line,
                    previous=previous,
                    next=next_text,
                )
            )
    return {"ok": not errors, "errors": [asdict(error) for error in errors]}


def auto_repair_boundaries(text: str) -> tuple[str, list[dict[str, Any]]]:
    paragraphs = _paragraphs_with_lines(text)
    if not paragraphs:
        return text, []

    repaired_paragraphs: list[tuple[int, str]] = []
    repairs: list[AutoRepair] = []
    idx = 0
    while idx < len(paragraphs):
        line, current = paragraphs[idx]
        if idx + 1 >= len(paragraphs):
            repaired_paragraphs.append((line, current))
            break
        _next_line, next_text = paragraphs[idx + 1]

        if _is_connector_duplication(current, next_text):
            next_clean = re.sub(r"^just\s+as\s+", "", next_text.strip(), count=1, flags=re.IGNORECASE)
            merged = f"{current.rstrip()} {next_clean}".strip()
            repairs.append(
                AutoRepair("BOUNDARY_DUPLICATION_REPAIR", line, current, next_text, merged)
            )
            repaired_paragraphs.append((line, merged))
            idx += 2
            continue

        if _is_mechanical_incomplete(current, next_text):
            merged = _join_fragments(current, next_text)
            repairs.append(
                AutoRepair("INCOMPLETE_SENTENCE_BOUNDARY_REPAIR", line, current, next_text, merged)
            )
            repaired_paragraphs.append((line, merged))
            idx += 2
            continue

        repaired_paragraphs.append((line, current))
        idx += 1

    repaired_text = "\n\n".join(paragraph for _line, paragraph in repaired_paragraphs).rstrip() + "\n"
    return repaired_text, [asdict(repair) for repair in repairs]


def write_boundary_report(path: Path, report: dict[str, Any]) -> Path:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
