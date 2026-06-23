from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


STAGE_NAME = "final_superscript_cleanup"
EXPECTED_BOOK_ORDER = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]

PREFIX_MAP = {
    "G": "ᴳ",
    "N": "ᴺ",
    "P": "ᴾ",
    "D": "ᴰ",
    "C": "ᶜ",
}

DIGIT_MAP = {
    "0": "⁰",
    "1": "¹",
    "2": "²",
    "3": "³",
    "4": "⁴",
    "5": "⁵",
    "6": "⁶",
    "7": "⁷",
    "8": "⁸",
    "9": "⁹",
}

MARKER_RE = re.compile(r"\[(G|N|P|D|C)([0-9]{2})\]")
RAW_GREEK_RE = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]+")
BOOK_RE = re.compile(r"^\s*(?:#{1,6}\s*)?BOOK\s+([IVXLCDM]+)\b.*$", re.IGNORECASE | re.MULTILINE)
SELF_MASTERY_RE = re.compile(
    r"Self-Mastery|self-mastery|self-mastering|perfect self-mastery|perfected self-mastery",
    re.IGNORECASE,
)
FORBIDDEN_PLACEHOLDERS = (
    "{{GLOSS:",
    "Greek term [G",
    "Greek phrase [G",
    "Greek word [G",
    "Greek term ᴳ",
    "Greek phrase ᴳ",
    "Greek word ᴳ",
)


def superscript_marker(prefix: str, digits: str) -> str:
    return PREFIX_MAP[prefix] + "".join(DIGIT_MAP[digit] for digit in digits)


def convert_markers(text: str) -> tuple[str, int, dict[str, int]]:
    counts = {prefix: 0 for prefix in PREFIX_MAP}

    def _replace(match: re.Match[str]) -> str:
        prefix = match.group(1)
        counts[prefix] += 1
        return superscript_marker(prefix, match.group(2))

    output = MARKER_RE.sub(_replace, text)
    return output, sum(counts.values()), counts


def apply_exact_fixes(text: str) -> tuple[str, dict[str, int]]:
    output = text
    fixes = {
        "middle_men_duplicate_removed": 0,
        "the_the_noble_fixed": 0,
        "nested_movements_brackets_fixed": 0,
    }

    middle_men_patterns = (
        ("middle-men [G08], or middle-men", "middle-men [G08]"),
        ("middle-men ᴳ⁰⁸, or middle-men", "middle-men ᴳ⁰⁸"),
    )
    for source, target in middle_men_patterns:
        count = output.count(source)
        if count:
            output = output.replace(source, target)
            fixes["middle_men_duplicate_removed"] += count

    nested_patterns = (
        (
            "those [movements [G34] or comings-to-be [G35]] that tend",
            "those movements [G34], or comings-to-be [G35], that tend",
        ),
        (
            "those [movements ᴳ³⁴ or comings-to-be ᴳ³⁵] that tend",
            "those movements ᴳ³⁴, or comings-to-be ᴳ³⁵, that tend",
        ),
    )
    for source, target in nested_patterns:
        count = output.count(source)
        if count:
            output = output.replace(source, target)
            fixes["nested_movements_brackets_fixed"] += count

    output, count = re.subn(r"\bthe\s+the\s+noble\b", "the noble", output)
    fixes["the_the_noble_fixed"] += count

    return output, fixes


def detected_book_order(text: str) -> list[str]:
    return [match.group(1).upper() for match in BOOK_RE.finditer(text)]


def _note_markers(text: str) -> list[str]:
    return re.findall(r"\[\d+\]", text)


def _count_forbidden_placeholders(text: str) -> int:
    return sum(text.count(placeholder) for placeholder in FORBIDDEN_PLACEHOLDERS)


def validate_text(original: str, text: str) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    book_order = detected_book_order(text)
    raw_greek_remaining = len(RAW_GREEK_RE.findall(text))
    old_gloss_markers_remaining = text.count("{{GLOSS:")
    greek_placeholders_remaining = _count_forbidden_placeholders(text)
    self_mastery_residue_remaining = len(SELF_MASTERY_RE.findall(text))
    square_bracket_glossary_markers_remaining = len(MARKER_RE.findall(text))
    note_markers_preserved = _note_markers(original) == _note_markers(text)
    book_order_valid = book_order == EXPECTED_BOOK_ORDER

    if raw_greek_remaining:
        errors.append("raw_greek_remaining")
    if old_gloss_markers_remaining:
        errors.append("old_gloss_markers_remaining")
    if greek_placeholders_remaining:
        errors.append("greek_placeholders_remaining")
    if self_mastery_residue_remaining:
        errors.append("self_mastery_residue_remaining")
    if square_bracket_glossary_markers_remaining:
        errors.append("square_bracket_glossary_markers_remaining")
    if not note_markers_preserved:
        errors.append("note_markers_changed")
    if not book_order_valid:
        errors.append("book_order_invalid")
    if len(book_order) != len(EXPECTED_BOOK_ORDER):
        errors.append("book_headings_count_invalid")

    validation = {
        "raw_greek_remaining": raw_greek_remaining,
        "old_gloss_markers_remaining": old_gloss_markers_remaining,
        "greek_placeholders_remaining": greek_placeholders_remaining,
        "self_mastery_residue_remaining": self_mastery_residue_remaining,
        "square_bracket_glossary_markers_remaining": square_bracket_glossary_markers_remaining,
        "book_order_valid": book_order_valid,
        "book_headings_count": len(book_order),
        "book_order_detected": [f"BOOK {book}" for book in book_order],
        "note_markers_preserved": note_markers_preserved,
    }
    return validation, warnings, errors


def run_cleanup(input_path: Path, *, output_path: Path, report_path: Path) -> dict[str, Any]:
    original = input_path.read_text(encoding="utf-8")
    text, markers_total, markers_by_prefix = convert_markers(original)
    text, fixes_applied = apply_exact_fixes(text)
    validation, warnings, errors = validate_text(original, text)
    status = "PASSED" if not errors else "FAILED"

    report = {
        "stage": STAGE_NAME,
        "status": status,
        "input_file": input_path.name,
        "input_path": str(input_path),
        "output_file": output_path.name,
        "output_path": str(output_path),
        "markers_converted_total": markers_total,
        "markers_converted_by_prefix": markers_by_prefix,
        "fixes_applied": fixes_applied,
        "validation": validation,
        "warnings": warnings,
        "errors": errors,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if status == "PASSED":
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return report


def default_output_paths(input_path: Path) -> tuple[Path, Path]:
    directory = input_path.parent
    return (
        directory / "book_0029_nicomachean_ethics_en_us_FINAL_SUPERSCRIPT_MARKED.txt",
        directory / "book_0029_nicomachean_ethics_final_superscript_cleanup_report.json",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run final superscript marker cleanup for EN-US Aristotle text.")
    parser.add_argument("input")
    parser.add_argument("--output", default=None)
    parser.add_argument("--report-output", default=None)
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_default, report_default = default_output_paths(input_path)
    report = run_cleanup(
        input_path,
        output_path=Path(args.output) if args.output else output_default,
        report_path=Path(args.report_output) if args.report_output else report_default,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
