from __future__ import annotations

import re
from typing import Dict

from . import paths


def _read_final_txt(edition) -> str:
    path = paths.final_merge_txt_path(edition)
    if path is None:
        raise FileNotFoundError("No merge_* file found. Run translate/refine/polish first.")
    if not path.exists():
        raise FileNotFoundError(f"Final TXT not found: {path}")
    return path.read_text(encoding="utf-8")


def txt_to_markdown(body_txt: str) -> str:
    # Convert paragraph markers into real paragraph breaks.
    text = re.sub(r"\s*@@P\d{4}@@\s*", "\n\n", body_txt)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    lines = text.splitlines()
    md_lines = []

    chapter_pattern = re.compile(
        r"^(CHAPTER\s+[IVXLC\d]+|CAPITULO\s+\d+|Chapter\s+\d+|Parte\s+\d+)$",
        re.IGNORECASE,
    )

    for line in lines:
        stripped = line.strip()
        if not stripped:
            md_lines.append("")
            continue

        if chapter_pattern.match(stripped):
            md_lines.append("")
            md_lines.append(f"# {stripped}")
            md_lines.append("")
            continue

        if stripped.isupper() and 5 <= len(stripped) <= 80:
            md_lines.append("")
            md_lines.append(f"## {stripped.title()}")
            md_lines.append("")
            continue

        md_lines.append(stripped)

    return "\n".join(md_lines).strip() + "\n"


def run_txt_to_md(edition) -> Dict[str, str]:
    raw_txt = _read_final_txt(edition)
    md_text = txt_to_markdown(raw_txt)

    out_path = paths.pre_qa_md_path(edition)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md_text, encoding="utf-8")

    return {
        "md_text": md_text,
        "path": str(out_path),
    }
