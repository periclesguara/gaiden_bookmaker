#!/usr/bin/env python3
"""Clean OCR text for The Russian Revolutionary Tradition.

Inputs:
  data/raw/The Russian Revolutionary Tradition.txt

Outputs:
  data/cleaned/the_russian_revolutionary_tradition_cleaned.txt
  data/cleaned/the_russian_revolutionary_tradition_cleaning_report.json

The cleaner removes mechanical OCR artifacts only. It preserves order,
frontmatter, notes, bibliography/index material, and authorial wording.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = REPO_ROOT / "data/raw/The Russian Revolutionary Tradition.txt"
OUTPUT_DIR = REPO_ROOT / "data/cleaned"
CLEANED_PATH = OUTPUT_DIR / "the_russian_revolutionary_tradition_cleaned.txt"
REPORT_PATH = OUTPUT_DIR / "the_russian_revolutionary_tradition_cleaning_report.json"

WORD_RE = re.compile(r"\b[\w’'-]+\b", re.UNICODE)
PAGE_NUMBER_RE = re.compile(r"^\s*(?:-+\s*)?\d{1,4}(?:\s*-+)?\s*$")
ROMAN_RE = re.compile(r"^[IVXLCDM]{1,12}\.?$", re.IGNORECASE)
CHAPTER_RE = re.compile(
    r"^(?:chapter|chapitre|book|part|section|appendix|introduction|contents|"
    r"name index|subject index|in place of a conclusion)\b",
    re.IGNORECASE,
)
ODD_CHARACTER_RE = re.compile(
    r"[�□■●◆]|[^\x09\x0a\x0d\x20-\x7e\u00a0-\u024f\u0370-\u03ff\u0400-\u04ff\u2010-\u201f\u2122©]"
)
MIXED_ALPHA_NUMERIC_RE = re.compile(r"\b(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z0-9]{3,}\b")
CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")


@dataclass
class CleanStats:
    removed_page_number_lines: int = 0
    removed_running_header_lines: int = 0
    joined_hyphenated_line_breaks: int = 0


def normalize_unicode(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    replacements = {
        "\ufeff": "",
        "\u00a0": " ",
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def estimated_token_count(text: str) -> int:
    return math.ceil(len(text) / 4)


def canonical_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())


def is_page_number_line(line: str) -> bool:
    stripped = line.strip()
    if not PAGE_NUMBER_RE.match(stripped):
        return False
    try:
        value = int(stripped.strip("- "))
    except ValueError:
        return False
    return 1 <= value <= 1200


def looks_like_heading(line: str, *, allow_title_case: bool = False) -> bool:
    text = canonical_line(line)
    if not text:
        return False
    if len(text) > 120:
        return False
    if CHAPTER_RE.match(text):
        return True
    if ROMAN_RE.match(text):
        return True
    words = [w for w in re.split(r"\s+", text) if w]
    if len(words) <= 12 and text.isupper() and any(ch.isalpha() for ch in text):
        return True
    if not allow_title_case:
        return False
    title_words = 0
    alpha_words = 0
    for word in words:
        clean = word.strip("'\".,:;!?()[]")
        if not clean or not any(ch.isalpha() for ch in clean):
            continue
        alpha_words += 1
        if clean[:1].isupper():
            title_words += 1
    return bool(alpha_words and alpha_words <= 10 and title_words / alpha_words >= 0.7)


def detect_repeated_running_lines(lines: list[str]) -> set[str]:
    counts = Counter(canonical_line(line) for line in lines if canonical_line(line))
    repeated: set[str] = set()
    protected = {
        "CONTENTS",
        "INTRODUCTION",
        "Name Index",
        "Subject Index",
        "In Place of a Conclusion",
    }
    for text, count in counts.items():
        if count < 3:
            continue
        if text in protected:
            continue
        if len(text) > 90:
            continue
        if text.lower().startswith(("chapter ", "name index", "subject index")):
            continue
        repeated.add(text)
    return repeated


def strip_mechanical_lines(lines: list[str], stats: CleanStats) -> list[str]:
    repeated = detect_repeated_running_lines(lines)
    cleaned: list[str] = []
    seen_repeated: Counter[str] = Counter()
    skip_leading_blank_after_page_number = False
    for index, line in enumerate(lines):
        text = canonical_line(line)
        if skip_leading_blank_after_page_number and not text:
            continue
        skip_leading_blank_after_page_number = False
        if is_page_number_line(text):
            while cleaned and cleaned[-1] == "":
                cleaned.pop()
            stats.removed_page_number_lines += 1
            skip_leading_blank_after_page_number = True
            continue
        if text in repeated:
            seen_repeated[text] += 1
            if index > 80:
                stats.removed_running_header_lines += 1
                continue
        line = re.sub(r"[ \t]+", " ", line.strip())
        cleaned.append(line)
    return cleaned


def append_joined(paragraph_lines: list[str], output: list[str], stats: CleanStats) -> None:
    if not paragraph_lines:
        return
    paragraph = paragraph_lines[0].strip()
    for raw_next in paragraph_lines[1:]:
        nxt = raw_next.strip()
        if not nxt:
            continue
        if re.search(r"[A-Za-z]-$", paragraph) and re.match(r"^[a-z]", nxt):
            paragraph = paragraph[:-1] + nxt
            stats.joined_hyphenated_line_breaks += 1
        elif re.search(r"[-/(\[]$", paragraph):
            paragraph += nxt
        else:
            paragraph += " " + nxt
    paragraph = re.sub(r"\s+([,.;:!?])", r"\1", paragraph)
    paragraph = re.sub(r"([([{])\s+", r"\1", paragraph)
    paragraph = re.sub(r"\s+([])}])", r"\1", paragraph)
    paragraph = re.sub(r"\s{2,}", " ", paragraph).strip()
    if paragraph:
        output.append(paragraph)


def join_wrapped_paragraphs(lines: list[str], stats: CleanStats) -> str:
    output: list[str] = []
    paragraph: list[str] = []
    previous_blank = True

    def flush_paragraph() -> None:
        append_joined(paragraph, output, stats)
        paragraph.clear()

    for line in lines:
        text = canonical_line(line)
        if not text:
            flush_paragraph()
            if output and output[-1] != "":
                output.append("")
            previous_blank = True
            continue

        heading = looks_like_heading(text, allow_title_case=previous_blank)
        if heading:
            flush_paragraph()
            if output and output[-1] != "":
                output.append("")
            output.append(text)
            output.append("")
            previous_blank = False
            continue

        if previous_blank and len(text) <= 80 and CHAPTER_RE.search(text):
            flush_paragraph()
            if output and output[-1] != "":
                output.append("")
            output.append(text)
            output.append("")
            previous_blank = False
            continue

        paragraph.append(text)
        previous_blank = False

    flush_paragraph()
    cleaned = "\n".join(output)
    cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned)
    return cleaned.strip() + "\n"


def suspicious_fragments(text: str, limit: int = 120) -> list[dict[str, str | int]]:
    findings: list[dict[str, str | int]] = []
    seen: set[tuple[int, str]] = set()
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        reasons: list[str] = []
        if ODD_CHARACTER_RE.search(stripped):
            reasons.append("odd OCR character")
        mixed_tokens = MIXED_ALPHA_NUMERIC_RE.findall(stripped)
        suspicious_mixed_tokens = [
            token
            for token in mixed_tokens
            if not re.match(r"^\d+(?:st|nd|rd|th)$", token, re.IGNORECASE)
        ]
        if suspicious_mixed_tokens:
            reasons.append("mixed alphanumeric OCR fragment")
        if CYRILLIC_RE.search(stripped):
            reasons.append("Cyrillic/OCR frontmatter or scan residue")
        if re.search(r"\b[a-z]\s+[a-z]\s+[a-z]\s+[a-z]\b", stripped):
            reasons.append("possible broken word spacing")
        if re.search(r"\b[bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ]{6,}\b", stripped):
            reasons.append("consonant-heavy fragment")
        if re.search(r"\s[,.;:!?]", stripped):
            reasons.append("space before punctuation")
        if reasons:
            key = (line_no, stripped[:120])
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                {
                    "line": line_no,
                    "text": stripped[:220],
                    "reason": "; ".join(reasons),
                }
            )
            if len(findings) >= limit:
                break
    return findings


def clean_text(original: str) -> tuple[str, CleanStats]:
    stats = CleanStats()
    normalized = normalize_unicode(original)
    stripped_lines = strip_mechanical_lines(normalized.split("\n"), stats)
    cleaned = join_wrapped_paragraphs(stripped_lines, stats)
    return cleaned, stats


def build_report(original: str, cleaned: str, stats: CleanStats) -> dict:
    original_chars = len(original)
    cleaned_chars = len(cleaned)
    original_tokens = estimated_token_count(original)
    cleaned_tokens = estimated_token_count(cleaned)
    percentage_delta = (
        ((cleaned_tokens - original_tokens) / original_tokens) * 100 if original_tokens else 0.0
    )
    return {
        "input_path": str(INPUT_PATH.relative_to(REPO_ROOT)),
        "cleaned_output_path": str(CLEANED_PATH.relative_to(REPO_ROOT)),
        "report_output_path": str(REPORT_PATH.relative_to(REPO_ROOT)),
        "original_character_count": original_chars,
        "cleaned_character_count": cleaned_chars,
        "original_word_count": word_count(original),
        "cleaned_word_count": word_count(cleaned),
        "estimated_original_token_count": original_tokens,
        "estimated_cleaned_token_count": cleaned_tokens,
        "percentage_delta": round(percentage_delta, 4),
        "within_97_103_percent_token_target": 97 <= (cleaned_tokens / original_tokens * 100) <= 103
        if original_tokens
        else False,
        "removed_page_number_lines": stats.removed_page_number_lines,
        "removed_running_header_lines": stats.removed_running_header_lines,
        "joined_hyphenated_line_breaks": stats.joined_hyphenated_line_breaks,
        "suspicious_ocr_fragments_requiring_manual_review": suspicious_fragments(cleaned),
    }


def main() -> int:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_PATH}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    original = INPUT_PATH.read_text(encoding="utf-8")
    cleaned, stats = clean_text(original)
    CLEANED_PATH.write_text(cleaned, encoding="utf-8")
    report = build_report(original, cleaned, stats)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Cleaned text: {CLEANED_PATH}")
    print(f"Report: {REPORT_PATH}")
    print(
        "Token delta: "
        f"{report['percentage_delta']}% "
        f"({report['estimated_original_token_count']} -> {report['estimated_cleaned_token_count']})"
    )
    print(f"Removed page-number lines: {report['removed_page_number_lines']}")
    print(f"Joined hyphenated breaks: {report['joined_hyphenated_line_breaks']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
