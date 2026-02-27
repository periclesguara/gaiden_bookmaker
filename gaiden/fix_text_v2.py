from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from gaiden.lint_headings import lint_headings


RE_HYPHEN_LINEBREAK = re.compile(r"(\w)-\n(\w)")
RE_MULTISPACE = re.compile(r"[ \t]{2,}")
RE_BAD_NBSP = re.compile(r"\u00A0+")

RE_CHAPTER = re.compile(r"^\s*(CHAPTER|Chapter)\s+([IVXLC]+|\d+)\b.*$")
RE_SECTION_NUM = re.compile(r"^\s*(\d+)\s*([.\-–—:])\s+(.+)$")


@dataclass
class FixReport:
    input_path: str
    output_path: str
    changes: dict
    lint_before: dict
    lint_after: dict


def _prep_text(text: str) -> str:
    text = RE_BAD_NBSP.sub(" ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = RE_HYPHEN_LINEBREAK.sub(r"\1\2", text)
    text = RE_MULTISPACE.sub(" ", text)
    return text


def _normalize_headings(lines: list[str]) -> tuple[list[str], dict]:
    changed = {"chapter_headings": 0, "section_headings": 0, "blanklines_inserted": 0}
    out: list[str] = []

    def ensure_blankline_before() -> None:
        if out and out[-1].strip() != "":
            out.append("")
            changed["blanklines_inserted"] += 1

    for raw in lines:
        line = raw.rstrip()

        if RE_CHAPTER.match(line):
            ensure_blankline_before()
            out.append(f"# {line.strip()}")
            out.append("")
            changed["chapter_headings"] += 1
            continue

        m2 = RE_SECTION_NUM.match(line)
        if m2 and not line.lstrip().startswith("#"):
            if len(line.strip()) <= 80:
                ensure_blankline_before()
                out.append(f"## {line.strip()}")
                out.append("")
                changed["section_headings"] += 1
                continue

        out.append(line)

    return out, changed


def fix_text_file(input_path: Path, output_path: Path, report_path: Path) -> FixReport:
    src = input_path.read_text(encoding="utf-8", errors="replace")

    issues_before = lint_headings(src)

    prepared = _prep_text(src)
    lines = prepared.splitlines()
    fixed_lines, changes = _normalize_headings(lines)
    fixed = "\n".join(fixed_lines).strip() + "\n"

    issues_after = lint_headings(fixed)

    rep = FixReport(
        input_path=str(input_path),
        output_path=str(output_path),
        changes=changes,
        lint_before={"count": len(issues_before), "issues": [asdict(x) for x in issues_before[:200]]},
        lint_after={"count": len(issues_after), "issues": [asdict(x) for x in issues_after[:200]]},
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(fixed, encoding="utf-8")
    report_path.write_text(json.dumps(asdict(rep), ensure_ascii=False, indent=2), encoding="utf-8")
    return rep
