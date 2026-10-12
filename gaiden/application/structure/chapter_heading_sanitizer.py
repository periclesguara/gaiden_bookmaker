from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


HEADING_RE = re.compile(r"^\s*(CHAPTER|Chapter)\s+(\d+)\s*$")


def _collapse_excess_blank_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    blank_count = 0
    for line in lines:
        if line.strip():
            blank_count = 0
            out.append(line)
            continue
        blank_count += 1
        if blank_count <= 1:
            out.append(line)
    return out


def _validate_accepted_sequence(accepted: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    expected = 1
    for item in accepted:
        chapter = int(item["chapter"])
        if chapter != expected:
            raise ValueError(
                f"Accepted chapter sequence is invalid: expected Chapter {expected}, found Chapter {chapter}."
            )
        expected += 1
    if not accepted:
        warnings.append("no_accepted_chapter_headings")
    return warnings


def sanitize_chapter_headings(
    text: str,
    *,
    allow_gaps: bool = False,
) -> tuple[str, dict[str, Any]]:
    lines = text.splitlines()
    had_final_newline = text.endswith("\n")

    accepted: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    warnings: list[str] = []
    out_lines: list[str] = []
    last_accepted_chapter = 0
    candidate_count = 0

    for line_number, line in enumerate(lines, start=1):
        match = HEADING_RE.match(line)
        if not match:
            out_lines.append(line)
            continue

        candidate_count += 1
        chapter = int(match.group(2))
        normalized = f"Chapter {chapter}"

        should_accept = False
        reason = ""
        if not accepted:
            if chapter == 1:
                should_accept = True
            else:
                reason = "skipped_heading"
                warnings.append(
                    f"line {line_number}: first accepted heading must be Chapter 1; removed Chapter {chapter}"
                )
        elif chapter == last_accepted_chapter + 1:
            should_accept = True
        elif allow_gaps and chapter > last_accepted_chapter:
            should_accept = True
            warnings.append(
                f"line {line_number}: accepted heading gap from Chapter {last_accepted_chapter} to Chapter {chapter}"
            )
        elif chapter <= last_accepted_chapter:
            reason = "backward_or_duplicate_heading"
        else:
            reason = "skipped_heading"
            warnings.append(
                f"line {line_number}: removed skipped heading Chapter {chapter}; expected Chapter {last_accepted_chapter + 1}"
            )

        if should_accept:
            out_lines.append(normalized)
            accepted.append(
                {
                    "line": line_number,
                    "original": line,
                    "normalized": normalized,
                    "chapter": chapter,
                }
            )
            last_accepted_chapter = chapter
            continue

        removed.append(
            {
                "line": line_number,
                "original": line,
                "chapter": chapter,
                "reason": reason,
            }
        )

    out_lines = _collapse_excess_blank_lines(out_lines)
    warnings.extend(_validate_accepted_sequence(accepted))

    fixed = "\n".join(out_lines)
    if had_final_newline and fixed:
        fixed += "\n"
    elif had_final_newline and not fixed:
        fixed = "\n"

    report: dict[str, Any] = {
        "accepted": accepted,
        "removed": removed,
        "warnings": warnings,
        "summary": {
            "candidate_headings": candidate_count,
            "accepted_count": len(accepted),
            "removed_count": len(removed),
        },
    }
    return fixed, report


def _run_cli() -> int:
    parser = argparse.ArgumentParser(description="Remove duplicated/backward/internal chapter headings.")
    parser.add_argument("--input", required=True, help="Input UTF-8 text file.")
    parser.add_argument("--output", required=True, help="Output UTF-8 corrected text file.")
    parser.add_argument("--report", required=True, help="Output JSON report path.")
    parser.add_argument("--allow-gaps", action="store_true", help="Allow accepted chapter number gaps.")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report)

    fixed, report = sanitize_chapter_headings(
        input_path.read_text(encoding="utf-8"),
        allow_gaps=bool(args.allow_gaps),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(fixed, encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_cli())
