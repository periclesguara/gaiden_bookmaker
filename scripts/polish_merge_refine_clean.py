#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from gaiden.openai_client import get_client

MODEL = "gpt-5.4"
MAX_OUTPUT_TOKENS = 6000

SYSTEM_PROMPT = """You are a senior literary line editor.

Apply one disciplined commercial native-smoothing pass to the provided excerpt from The Valley of Fear.

Non-negotiable:
- Preserve plot, clues, chronology, deductions, names, tone, Holmes/Watson voice, and Sherlockian atmosphere.
- Do not rewrite from scratch.
- Do not add content.
- Do not remove lightly literary/classic flavour.
- Prefer micro-edits over paragraph rewrites.
- Keep British-neutral literary English fully readable for US readers.
- Return idiomatic, native-feeling prose.
- Preserve paragraph breaks exactly.

Output valid JSON only with this schema:
{
  "revised_text": "full revised excerpt",
  "high_value_fixes": [{"original": "...", "revised": "...", "reason": "..."}],
  "optional_tweaks": [{"original": "...", "revised": "...", "reason": "..."}],
  "left_intact_on_purpose": [{"original": "...", "reason": "..."}]
}

Rules for the lists:
- Include only substantial items worth logging.
- Keep each list concise.
- If a section has nothing notable, return an empty list.
"""

USER_TEMPLATE = """TASK: Apply final commercial native-smoothing pass to The Valley of Fear.

PRIMARY GOAL
Make the prose read more naturally for UK and US commercial readers while preserving plot, tone, Holmes/Watson voice, and Sherlockian atmosphere.

DO NOT
- Do not rewrite the book from scratch.
- Do not simplify the prose into generic modern thriller English.
- Do not fully Americanize the voice.
- Do not alter clues, facts, chronology, names, or deductions.
- Do not expand scenes or add content.
- Do not remove the lightly literary/classic flavor.

TARGET REGISTER
- Modern literary English
- British-neutral base
- Smooth and fully readable for US readers
- Commercial, fluent, native-feeling
- Not academic
- Not archaic
- Not slangy

MANDATORY EDIT RULES
1. Shorten sentences that feel overly explained or over-engineered.
2. Replace “correct but translated-sounding” phrasing with idiomatic literary English.
3. Reduce balanced, symmetrical sentence patterns when they appear too often in sequence.
4. Tighten dialogue so it sounds more spontaneous and less carefully composed.
5. Keep Holmes articulate, sharp, ironic, and slightly theatrical.
6. Keep Watson clear, intelligent, observant, and mildly formal.
7. Preserve emotional tension and scene momentum.
8. Prefer micro-edits over paragraph rewrites.
9. Leave good literary phrasing intact unless it creates friction.
10. When in doubt, preserve atmosphere over simplification.

QUALITY CHECKS
1. Does it sound like native literary English?
2. Is it smoother without becoming generic?
3. Does it preserve Sherlockian identity?
4. Would a UK or US trade reader read it without friction?
5. Was meaning preserved exactly?
6. Did the line become more alive, not just shorter?

Excerpt id: {part_name}

TEXT
{text}
"""


def _extract_json(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError("Model did not return JSON.")
    return json.loads(candidate[start : end + 1])


def _call_polish(client: Any, part_name: str, text: str) -> dict[str, Any]:
    resp = client.responses.create(
        model=MODEL,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_TEMPLATE.format(part_name=part_name, text=text),
            },
        ],
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    output_text = getattr(resp, "output_text", "") or ""
    if not output_text:
        try:
            output_text = resp.output[0].content[0].text
        except Exception as exc:
            raise RuntimeError(f"No output_text for {part_name}") from exc
    payload = _extract_json(output_text)
    revised = str(payload.get("revised_text") or "").strip()
    if not revised:
        raise RuntimeError(f"Empty revised_text for {part_name}")
    payload["revised_text"] = revised + ("\n" if not revised.endswith("\n") else "")
    return payload


def _changes_md(title: str, items: list[dict[str, str]], include_revised: bool = True) -> list[str]:
    lines = [f"## {title}", ""]
    if not items:
        lines.append("None.")
        lines.append("")
        return lines
    for idx, item in enumerate(items, start=1):
        lines.append(f"### {idx}.")
        lines.append(f"ORIGINAL: {item.get('original', '').strip()}")
        if include_revised:
            lines.append(f"REVISED: {item.get('revised', '').strip()}")
        lines.append(f"REASON: {item.get('reason', '').strip()}")
        lines.append("")
    return lines


def run() -> None:
    root = ROOT
    parts_dir = root / "data" / "builds" / "book_018" / "en" / "split_by_chapter" / "return_aldebaran"
    out_dir = root / "data" / "translated" / "book_018"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "merge_refine_clean_polished.txt"
    changelog_path = out_dir / "merge_refine_clean_polish_changelog.md"

    part_paths = sorted(parts_dir.glob("chapter_*_part_*.txt"))
    if not part_paths:
        raise FileNotFoundError(f"No refined parts found in {parts_dir}")

    client = get_client()
    revised_parts: list[str] = []
    high_value: list[dict[str, str]] = []
    optional: list[dict[str, str]] = []
    left_intact: list[dict[str, str]] = []

    for path in part_paths:
        text = path.read_text(encoding="utf-8")
        print(f"[POLISH] {path.name}", flush=True)
        payload = _call_polish(client, path.name, text)
        revised_parts.append(payload["revised_text"].rstrip())
        for item in payload.get("high_value_fixes") or []:
            entry = dict(item)
            entry.setdefault("part", path.name)
            high_value.append(entry)
        for item in payload.get("optional_tweaks") or []:
            entry = dict(item)
            entry.setdefault("part", path.name)
            optional.append(entry)
        for item in payload.get("left_intact_on_purpose") or []:
            entry = dict(item)
            entry.setdefault("part", path.name)
            left_intact.append(entry)

    merged = "\n\n".join(part for part in revised_parts if part).strip() + "\n"
    out_path.write_text(merged, encoding="utf-8")

    md_lines = ["# Commercial Polish Change Log", ""]
    md_lines.extend(_changes_md("HIGH-VALUE FIXES", high_value, include_revised=True))
    md_lines.extend(_changes_md("OPTIONAL TWEAKS", optional, include_revised=True))
    md_lines.extend(_changes_md("LEFT INTACT ON PURPOSE", left_intact, include_revised=False))
    changelog_path.write_text("\n".join(md_lines).strip() + "\n", encoding="utf-8")

    print(f"[DONE] polished manuscript: {out_path}")
    print(f"[DONE] changelog: {changelog_path}")


if __name__ == "__main__":
    run()
