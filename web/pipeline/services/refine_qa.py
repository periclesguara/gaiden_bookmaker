from __future__ import annotations

import json
import re
from pathlib import Path

from . import paths


def _roman_to_int(value: str) -> int | None:
    tokens = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    s = value.upper().strip()
    if not s or any(ch not in tokens for ch in s):
        return None
    total = 0
    prev = 0
    for ch in reversed(s):
        n = tokens[ch]
        if n < prev:
            total -= n
        else:
            total += n
            prev = n
    return total if total > 0 else None


def _chapter_number_from_heading(line: str) -> int | None:
    m = re.match(r"^\s{0,3}(?:#+\s*)?(?:chapter)\s+([0-9]+|[ivxlc]+)\b", line, flags=re.IGNORECASE)
    if not m:
        return None
    token = m.group(1)
    if token.isdigit():
        return int(token)
    return _roman_to_int(token)


def _pick_source_text(edition) -> Path:
    build_dir = paths.edition_build_dir(edition)
    candidates = [
        paths.saved_core_reference_path(edition),
        build_dir / "merge_refine.txt",
        build_dir / "merge_polish.txt",
        build_dir / "merge_translate.txt",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.exists():
            return candidate
    for candidate in sorted(build_dir.glob("merge_*.txt")):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No merge text found for Refine QA in {build_dir}")


def _build_markdown_report(source_path: Path, qa_result: dict[str, object]) -> str:
    summary = qa_result["summary"]
    issues = qa_result["issues"]
    lines = [
        "# Refine QA Report",
        "",
        f"- Source: `{source_path}`",
        f"- Pass: `{qa_result['pass']}`",
        f"- Critical: `{summary['critical']}`",
        f"- Major: `{summary['major']}`",
        f"- Minor: `{summary['minor']}`",
        "",
        "## Findings",
    ]
    if not issues:
        lines.append("- No issues detected.")
        return "\n".join(lines) + "\n"
    for issue in issues:
        snippet = issue.get("snippet", "")
        if len(snippet) > 180:
            snippet = snippet[:177] + "..."
        lines.append(
            f"- [{issue['severity']}] {issue['code']}: {issue['message']}"
            + (f" (line {issue['line']})" if issue.get("line") else "")
            + (f" | `{snippet}`" if snippet else "")
        )
    return "\n".join(lines) + "\n"


def run_refine_qa(edition) -> dict[str, object]:
    source_path = _pick_source_text(edition)
    text = source_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    issues: list[dict[str, object]] = []

    last_chapter = 0
    heading_seen: set[str] = set()
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue

        chapter_no = _chapter_number_from_heading(stripped)
        if chapter_no is not None:
            key = re.sub(r"\s+", " ", stripped.lower())
            if key in heading_seen:
                issues.append(
                    {
                        "severity": "major",
                        "code": "duplicate_heading",
                        "message": "Duplicate chapter heading detected.",
                        "line": idx,
                        "snippet": stripped,
                    }
                )
            heading_seen.add(key)
            if last_chapter and chapter_no < last_chapter:
                issues.append(
                    {
                        "severity": "major",
                        "code": "chapter_order",
                        "message": "Chapter numbering appears out of order.",
                        "line": idx,
                        "snippet": stripped,
                    }
                )
            last_chapter = max(last_chapter, chapter_no)

    residue_patterns = [
        (r"⟦BEGIN_CHUNK", "sentinel_begin"),
        (r"⟦END_CHUNK", "sentinel_end"),
        (r"\bProject Gutenberg\b", "gutenberg_residue"),
        (r"\bHere is the rewrite\b", "assistant_prefix"),
        (r"\bAs an AI\b", "assistant_meta"),
    ]
    for pattern, code in residue_patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        line_no = text[: m.start()].count("\n") + 1
        snippet = lines[line_no - 1].strip() if 0 <= line_no - 1 < len(lines) else ""
        issues.append(
            {
                "severity": "major",
                "code": code,
                "message": "Residual marker/text found in refined content.",
                "line": line_no,
                "snippet": snippet,
            }
        )

    has_straight = '"' in text or "'" in text
    has_curly = "“" in text or "”" in text or "’" in text
    if has_straight and has_curly:
        issues.append(
            {
                "severity": "minor",
                "code": "mixed_quotes",
                "message": "Mixed straight and curly quotes detected.",
                "line": None,
                "snippet": "",
            }
        )

    summary = {
        "critical": sum(1 for it in issues if it["severity"] == "critical"),
        "major": sum(1 for it in issues if it["severity"] == "major"),
        "minor": sum(1 for it in issues if it["severity"] == "minor"),
    }
    passed = summary["critical"] == 0 and summary["major"] == 0

    json_path = paths.refine_qa_json_path(edition)
    md_path = paths.refine_qa_md_path(edition)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    qa_result = {
        "schema": "refine_qa_v1",
        "pass": passed,
        "source_path": str(source_path),
        "summary": summary,
        "issues": issues,
    }
    json_path.write_text(json.dumps(qa_result, ensure_ascii=True, indent=2), encoding="utf-8")
    md_path.write_text(_build_markdown_report(source_path, qa_result), encoding="utf-8")

    return {
        "pass": passed,
        "source_path": str(source_path),
        "summary": summary,
        "issues": issues,
        "json_path": str(json_path),
        "md_path": str(md_path),
    }
