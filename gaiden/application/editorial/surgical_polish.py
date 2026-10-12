from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SPELLING_MAP = {
    "labour": "labor",
    "Labour": "Labor",
    "labours": "labors",
    "Labours": "Labors",
    "laboured": "labored",
    "Laboured": "Labored",
    "labouring": "laboring",
    "Labouring": "Laboring",
    "honour": "honor",
    "Honour": "Honor",
    "honours": "honors",
    "Honours": "Honors",
    "honoured": "honored",
    "Honoured": "Honored",
    "honouring": "honoring",
    "Honouring": "Honoring",
    "honourable": "honorable",
    "Honourable": "Honorable",
    "honourably": "honorably",
    "Honourably": "Honorably",
    "favour": "favor",
    "Favour": "Favor",
    "favours": "favors",
    "Favours": "Favors",
    "favoured": "favored",
    "Favoured": "Favored",
    "favouring": "favoring",
    "Favouring": "Favoring",
    "favourable": "favorable",
    "Favourable": "Favorable",
    "unfavourable": "unfavorable",
    "Unfavourable": "Unfavorable",
    "characterised": "characterized",
    "Characterised": "Characterized",
    "characterise": "characterize",
    "Characterise": "Characterize",
    "characterises": "characterizes",
    "Characterises": "Characterizes",
    "characterising": "characterizing",
    "Characterising": "Characterizing",
    "defence": "defense",
    "Defence": "Defense",
    "defences": "defenses",
    "Defences": "Defenses",
    "offence": "offense",
    "Offence": "Offense",
    "offences": "offenses",
    "Offences": "Offenses",
    "pretence": "pretense",
    "Pretence": "Pretense",
    "pretences": "pretenses",
    "Pretences": "Pretenses",
    "theatre": "theater",
    "Theatre": "Theater",
    "theatres": "theaters",
    "Theatres": "Theaters",
    "harbour": "harbor",
    "Harbour": "Harbor",
    "harbours": "harbors",
    "Harbours": "Harbors",
    "harboured": "harbored",
    "Harboured": "Harbored",
    "travelling": "traveling",
    "Travelling": "Traveling",
    "travelled": "traveled",
    "Travelled": "Traveled",
    "traveller": "traveler",
    "Traveller": "Traveler",
    "travellers": "travelers",
    "Travellers": "Travelers",
    "grey": "gray",
    "Grey": "Gray",
    "greybeard": "graybeard",
    "Greybeard": "Graybeard",
    "skilful": "skillful",
    "Skilful": "Skillful",
    "practise": "practice",
    "Practise": "Practice",
    "practised": "practiced",
    "Practised": "Practiced",
    "practising": "practicing",
    "Practising": "Practicing",
    "licence": "license",
    "Licence": "License",
    "licences": "licenses",
    "Licences": "Licenses",
    "judgement": "judgment",
    "Judgement": "Judgment",
    "judgements": "judgments",
    "Judgements": "Judgments",
}

REGISTER_MAP = {
    "Damn it—what madness this is": "Good gods, what madness this is",
    "Damn it, what madness this is": "Good gods, what madness this is",
    "Damn it — what madness this is": "Good gods, what madness this is",
    "what is more crazy": "what is more foolish",
    "What is more crazy": "What is more foolish",
    "What can be more crazy": "What can be more foolish",
    "kids": "children",
    "Kids": "Children",
    "guy": "man",
    "Guy": "Man",
    "guys": "men",
    "Guys": "Men",
}

CLASSICAL_TERMS = [
    "sordida",
    "toga pulla",
    "praetexta",
    "laticlave",
    "fasces",
    "sine insignibus Magistratus",
    "perversa vestis",
    "invidiam facere alicui",
    "deminutio",
    "editur subscriptio",
    "Ira est cupiditas",
]

BROKEN_REFERENCE_PATTERNS = [
    r"\bBook\s*,",
    r"\bBook\s*,\s*Chapter\b",
    r"\bChapter\s*\.",
    r"\bAeneid\s*,\s*,",
    r"\bBC\s*(?:\n|$)",
    r"\bAUC\s*(?:\n|$)",
    r"\bparagraph\s*\.",
    r"\bp\.\s*(?:\n|$)",
]

VALID_CHAPTER_RE = re.compile(r"^Chapter\s+(\d+)$")
LINE_UPPER_HEADING_RE = re.compile(r"^\s*CHAPTER\s+\d+\s*$")
INLINE_HEADING_RE = re.compile(r"\s+CHAPTER\s+\d+\b")
URL_RE = re.compile(r"https?://\S+|file://\S+")
GREEK_RE = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")


def _is_url_or_path_line(line: str) -> bool:
    stripped = line.strip()
    return bool(URL_RE.search(line) or stripped.startswith(("/", "file://")) or re.match(r"^[A-Za-z]:[\\/]", stripped))


def _replace_word_boundary(line: str, original: str, replacement: str) -> tuple[str, int]:
    pattern = re.compile(rf"\b{re.escape(original)}\b")
    return pattern.subn(replacement, line)


def _fix_spacing_after_inline_heading_removal(line: str) -> str:
    line = re.sub(r"\s+,", ",", line)
    line = re.sub(r"\s+\.", ".", line)
    line = re.sub(r",{2,}", ",", line)
    line = re.sub(r" {2,}", " ", line)
    return line


def _apply_map_to_line(
    line: str,
    mapping: dict[str, str],
    records: list[dict[str, Any]],
) -> str:
    if _is_url_or_path_line(line):
        return line
    for original, replacement in mapping.items():
        line, count = _replace_word_boundary(line, original, replacement)
        if count:
            records.append({"original": original, "replacement": replacement, "count": count})
    return line


def _merge_replacement_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], int] = {}
    for record in records:
        key = (str(record["original"]), str(record["replacement"]))
        merged[key] = merged.get(key, 0) + int(record["count"])
    return [
        {"original": original, "replacement": replacement, "count": count}
        for (original, replacement), count in sorted(merged.items())
    ]


def _collect_broken_references(text: str) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for pattern in BROKEN_REFERENCE_PATTERNS:
        regex = re.compile(pattern)
        for match in regex.finditer(text):
            line = text[: match.start()].count("\n") + 1
            snippet = text[max(0, match.start() - 80) : match.end() + 80].replace("\n", " ")
            warnings.append({"line": line, "pattern": pattern, "snippet": snippet})
    return warnings


def _collect_classical_terms(text: str) -> list[dict[str, Any]]:
    terms: list[dict[str, Any]] = []
    for term in CLASSICAL_TERMS:
        for match in re.finditer(rf"\b{re.escape(term)}\b", text):
            terms.append(
                {
                    "line": text[: match.start()].count("\n") + 1,
                    "term": term,
                    "action": "preserved",
                }
            )
    for match in GREEK_RE.finditer(text):
        terms.append(
            {
                "line": text[: match.start()].count("\n") + 1,
                "term": match.group(0),
                "action": "preserved",
            }
        )
    return terms


def _endnotes_report(text: str, broken_reference_warnings: list[dict[str, Any]]) -> dict[str, Any]:
    marker = re.search(r"(?m)^Endnotes\s*$", text)
    if not marker:
        return {"present": False, "return_markers": 0, "broken_reference_warnings": []}
    endnote_text = text[marker.start() :]
    start_line = text[: marker.start()].count("\n") + 1
    endnote_warnings = [
        warning
        for warning in broken_reference_warnings
        if int(warning["line"]) >= start_line
    ]
    return {
        "present": True,
        "return_markers": endnote_text.count("↩︎"),
        "broken_reference_warnings": endnote_warnings,
    }


def _validate_output(input_text: str, output_text: str) -> dict[str, Any]:
    lines = output_text.splitlines()
    chapters: list[int] = []
    line_only_uppercase = 0
    for line in lines:
        if LINE_UPPER_HEADING_RE.match(line):
            line_only_uppercase += 1
        match = VALID_CHAPTER_RE.match(line)
        if match:
            chapters.append(int(match.group(1)))

    chapter_sequence_ok = chapters == list(range(1, len(chapters) + 1))
    inline_remaining = len(INLINE_HEADING_RE.findall(output_text))
    input_len = max(1, len(input_text))
    length_delta_percent = abs(len(output_text) - len(input_text)) / input_len * 100
    passed = (
        chapter_sequence_ok
        and line_only_uppercase == 0
        and inline_remaining == 0
        and length_delta_percent <= 2.0
    )
    return {
        "chapter_sequence_ok": chapter_sequence_ok,
        "line_only_uppercase_headings_remaining": line_only_uppercase,
        "inline_uppercase_headings_remaining": inline_remaining,
        "length_delta_percent": length_delta_percent,
        "passed": passed,
    }


def surgical_polish_text(
    text: str,
    *,
    american_english: bool = True,
    fix_modernisms: bool = True,
    fix_inline_heading_contamination: bool = True,
    normalize_roman_references: bool = True,
    greek_policy: str = "preserve",
) -> tuple[str, dict[str, Any]]:
    del normalize_roman_references, greek_policy
    spelling_records: list[dict[str, Any]] = []
    register_records: list[dict[str, Any]] = []
    inline_records: list[dict[str, Any]] = []
    out_lines: list[str] = []
    in_code_block = False

    for line_number, original_line in enumerate(text.splitlines(), start=1):
        line = original_line
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            out_lines.append(line)
            continue
        if in_code_block or VALID_CHAPTER_RE.match(line):
            out_lines.append(line)
            continue

        if fix_inline_heading_contamination and not LINE_UPPER_HEADING_RE.match(line):
            matches = list(INLINE_HEADING_RE.finditer(line))
            if matches:
                before = line
                removed = [m.group(0).strip() for m in matches]
                line = INLINE_HEADING_RE.sub("", line)
                line = _fix_spacing_after_inline_heading_removal(line)
                inline_records.extend(
                    {
                        "line": line_number,
                        "removed": item,
                        "before": before,
                        "after": line,
                    }
                    for item in removed
                )

        if american_english:
            line = _apply_map_to_line(line, SPELLING_MAP, spelling_records)
        if fix_modernisms:
            line = _apply_map_to_line(line, REGISTER_MAP, register_records)
        out_lines.append(line)

    output_text = "\n".join(out_lines)
    if text.endswith("\n"):
        output_text += "\n"

    broken_reference_warnings = _collect_broken_references(output_text)
    classical_terms = _collect_classical_terms(output_text)
    endnotes = _endnotes_report(output_text, broken_reference_warnings)
    validation = _validate_output(text, output_text)
    spelling = _merge_replacement_records(spelling_records)
    register = _merge_replacement_records(register_records)

    report: dict[str, Any] = {
        "summary": {
            "spelling_replacements": sum(item["count"] for item in spelling),
            "register_replacements": sum(item["count"] for item in register),
            "inline_heading_contaminations_removed": len(inline_records),
            "broken_reference_warnings": len(broken_reference_warnings),
            "greek_or_latin_terms_detected": len(classical_terms),
            "endnotes_present": bool(endnotes["present"]),
        },
        "spelling": spelling,
        "register": register,
        "inline_heading_contamination": inline_records,
        "broken_reference_warnings": broken_reference_warnings,
        "classical_terms": classical_terms,
        "endnotes": endnotes,
        "validation": validation,
    }
    return output_text, report


def _run_cli() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic surgical editorial polish.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report)
    text = input_path.read_text(encoding="utf-8")
    output, report = surgical_polish_text(text)
    report["input"] = str(input_path)
    report["output"] = str(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output, encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not report["validation"]["passed"]:
        print("Validation failed; canonical input was not overwritten.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_cli())
