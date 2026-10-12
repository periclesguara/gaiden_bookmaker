#!/usr/bin/env python3
"""Create a neutralized internal study edition of a Soviet-era source.

This script is intentionally conservative. It preserves structure and factual
sequence, avoids editing primary quotations, notes, and index entries, and uses
attribution/qualification for Soviet Marxist-Leninist framing.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = REPO_ROOT / "data/cleaned/the_russian_revolutionary_tradition_cleaned.txt"
OUTPUT_DIR = REPO_ROOT / "data/neutralized"
NEUTRALIZED_PATH = OUTPUT_DIR / "the_russian_revolutionary_tradition_neutralized_study_edition.txt"
AUDIT_PATH = OUTPUT_DIR / "the_russian_revolutionary_tradition_bias_audit.json"
BIAS_MAP_PATH = OUTPUT_DIR / "the_russian_revolutionary_tradition_bias_map.csv"
EDITORIAL_NOTES_PATH = OUTPUT_DIR / "the_russian_revolutionary_tradition_editorial_notes.md"

LEGAL_NOTICE = (
    "[Editorial notice: This is an internal neutralized study transformation of a copyrighted "
    "1988 translated Soviet-era secondary source. It is for research, analysis, citation "
    "extraction, and historical mapping only, and is not a publishable edition or public-domain text.]"
)

WORD_RE = re.compile(r"\b[\w’'-]+\b", re.UNICODE)
CHAPTER_RE = re.compile(r"^(Chapter\s+(?:One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|Eleven|Twelve|Thirteen|Fourteen)\.?)$", re.I)
STRUCTURAL_HEADING_RE = re.compile(
    r"^(?:CONTENTS|INTRODUCTION|In Place of a Conclusion|Name Index|Subject Index|"
    r"Chapter\s+(?:One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|Eleven|Twelve|Thirteen|Fourteen)\.?)$",
    re.I,
)
NOTE_RE = re.compile(
    r"^\d{1,3}\s*(?:[A-Z][A-Za-z.\-]*|V\.?I\.?|M\.?N\.?|See:|Cf\.|Ibid\.|Marx|Engels|Lenin|Herzen|Plekhanov)"
)
INDEX_ENTRY_RE = re.compile(r"^[A-Z][A-Za-z' .\-()]+,\s+\d")
QUOTE_START_RE = re.compile(r"^[\"']")


BIAS_PATTERNS: dict[str, tuple[str, ...]] = {
    "MARXIST_LENINIST_FRAME": (
        r"\bMarxist\b",
        r"\bMarxism\b",
        r"\bMarxist-Leninist\b",
        r"\bscientific socialism\b",
        r"\bhistorical materialism\b",
        r"\bproletarian\b",
        r"\bBolshevik\b",
        r"\bBolshevism\b",
        r"\bbourgeois(?:ie)?\b",
        r"\bclass struggle\b",
    ),
    "TELEOLOGICAL_HISTORY": (
        r"\bhistorical necessity\b",
        r"\bnatural consequence\b",
        r"\binevitable\b",
        r"\binevitably\b",
        r"\bculminated\b",
        r"\bpath leading to communism\b",
        r"\bpath to socialism\b",
        r"\bdoomed to perish\b",
        r"\bnot a fortuitous event\b",
    ),
    "SOVIET_POLEMIC": (
        r"\bSoviet historical science\b",
        r"\bSoviet researchers\b",
        r"\bLenin's method\b",
        r"\bLenin defined\b",
        r"\bcorrectly orientated\b",
        r"\bopportunist\b",
        r"\breactionary\b",
        r"\bprogressive forces\b",
    ),
    "ANTI_WESTERN_POLEMIC": (
        r"\banti-communist orientation\b",
        r"\banti-communist aim\b",
        r"\bWestern literature\b",
        r"\bWestern scholars\b",
        r"\bbourgeois historiography\b",
        r"\bSovietologists\b",
        r"\bnon-scientific\b",
    ),
    "CLASS_STRUGGLE_REDUCTION": (
        r"\bclass forces\b",
        r"\bclass relations\b",
        r"\bclass struggle\b",
        r"\bexploiter\b",
        r"\bexploitation\b",
        r"\bworking class\b",
        r"\bproletariat\b",
        r"\bpeasants?' government\b",
    ),
    "LENINIST_VALIDATION": (
        r"\bLenin\b",
        r"\bLenin's concept\b",
        r"\bLenin's method\b",
        r"\bLeninist\b",
        r"\bBolshevik\b",
        r"\bBolshevism\b",
        r"\bproletarian party of a new kind\b",
        r"\bright revolutionary theory\b",
    ),
    "BOLSHEVIK_TRIUMPHALISM": (
        r"\blead the masses to victory\b",
        r"\bto victory\b",
        r"\bthe victory of Marxism\b",
        r"\bthe victory in Russia of Marxism\b",
        r"\bunder the banner of Marxism\b",
        r"\bleading revolutionary forces\b",
    ),
}

COMPILED_BIAS_PATTERNS = {
    category: tuple(re.compile(pattern, re.I) for pattern in patterns)
    for category, patterns in BIAS_PATTERNS.items()
}


@dataclass
class ParagraphRecord:
    paragraph_id: int
    chapter: str
    original: str
    neutralized: str
    categories: list[str]
    action_taken: str
    confidence: str
    needs_manual_review: bool


@dataclass
class ValidationResult:
    recommendation: str
    failures: list[str]
    warnings: list[str]


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def estimated_token_count(text: str) -> int:
    return math.ceil(len(text) / 4)


def split_blocks(text: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", text.strip()) if block.strip()]


def excerpt(text: str, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def is_heading(block: str) -> bool:
    if "\n" in block:
        return False
    stripped = block.strip()
    if STRUCTURAL_HEADING_RE.match(stripped):
        return True
    if len(stripped) <= 90 and stripped.isupper() and any(ch.isalpha() for ch in stripped):
        return True
    if len(stripped) <= 110 and re.match(r"^[A-Z][A-Za-z' .:;\-()]+$", stripped):
        words = stripped.split()
        title_words = [word for word in words if word[:1].isupper()]
        return bool(words and len(words) <= 12 and len(title_words) / len(words) >= 0.65)
    return False


def is_bibliographic_or_note(block: str, chapter: str) -> bool:
    stripped = block.strip()
    if chapter in {"Name Index", "Subject Index"}:
        return True
    if NOTE_RE.match(stripped):
        return True
    if INDEX_ENTRY_RE.match(stripped):
        return True
    bibliographic_markers = (
        "Collected Works",
        "Moscow,",
        "Vol.",
        "Vols",
        "p.",
        "pp.",
        "in Russian",
        "Progress Publishers",
        "ISBN",
    )
    return stripped[:2].isdigit() and any(marker in stripped for marker in bibliographic_markers)


def is_primary_quote(block: str) -> bool:
    stripped = block.strip()
    if QUOTE_START_RE.match(stripped):
        return True
    quote_chars = stripped.count('"') + stripped.count("'")
    return len(stripped) > 120 and quote_chars >= 8 and quote_chars / max(len(stripped), 1) > 0.045


def is_frontmatter(block_id: int, block: str) -> bool:
    if block_id <= 28:
        return True
    return any(marker in block for marker in ("© Progress Publishers", "Translated from the Russian", "ISBN"))


def classify_block(block: str, chapter: str, block_id: int) -> list[str]:
    categories: list[str] = []
    if is_heading(block):
        categories.append("FACTUAL_NARRATIVE")
        return categories
    if is_frontmatter(block_id, block) or is_bibliographic_or_note(block, chapter):
        categories.append("BIBLIOGRAPHIC_OR_NOTE")
        return categories
    if is_primary_quote(block):
        categories.append("PRIMARY_QUOTE")

    for category, patterns in COMPILED_BIAS_PATTERNS.items():
        if any(pattern.search(block) for pattern in patterns):
            categories.append(category)

    ideological_categories = [c for c in categories if c not in {"PRIMARY_QUOTE", "BIBLIOGRAPHIC_OR_NOTE"}]
    if ideological_categories:
        categories.append("HISTORICAL_INTERPRETATION")
    elif "PRIMARY_QUOTE" not in categories:
        if any(marker in block for marker in ("therefore", "however", "in this sense", "it would seem")):
            categories.append("NEUTRAL_ANALYSIS")
        else:
            categories.append("FACTUAL_NARRATIVE")
    return sorted(set(categories), key=categories.index)


def split_protected_quote_segments(text: str) -> list[tuple[str, bool]]:
    quote_re = re.compile(r'("[^"\n]{1,900}"|(?<!\w)\'[^\'\n]{1,600}\'(?!\w))')
    segments: list[tuple[str, bool]] = []
    cursor = 0
    for match in quote_re.finditer(text):
        if match.start() > cursor:
            segments.append((text[cursor : match.start()], False))
        segments.append((match.group(0), True))
        cursor = match.end()
    if cursor < len(text):
        segments.append((text[cursor:], False))
    return segments


def apply_segment_replacements(segment: str) -> str:
    replacements: tuple[tuple[str, str], ...] = (
        (
            r"\bThe October Revolution of 1917, coming as the conclusion of a great historical struggle, took the country along the path of socialist development\.",
            "The authors present the October Revolution of 1917 as the conclusion of a long historical struggle that led Russia along the path of socialist development.",
        ),
        (
            r"\bSoviet researchers gradually mastered Lenin's method of studying the Russian liberation movement, and used it to examine\b",
            "Soviet researchers increasingly used the interpretive method associated with Lenin in Soviet historiography to examine",
        ),
        (
            r"\banti-communist orientation\b",
            "anti-communist or liberal orientation",
        ),
        (
            r"\bimmature revolutionarism\b",
            "earlier revolutionary politics, characterized by the authors as immature revolutionarism",
        ),
        (
            r"\bRussia adopted the path of socialist development, the path leading to communism\b",
            "After 1917, the Bolshevik regime claimed to place Russia on a socialist path, later described by Soviet historiography as leading toward communism",
        ),
        (
            r"\bthe movement of the country along the path to socialism had become a historical necessity\b",
            "the authors present the country's movement toward socialism as a historical necessity",
        ),
        (
            r"\bhistorical necessity\b",
            "historical necessity in the authors' interpretation",
        ),
        (
            r"\bnatural consequence\b",
            "outcome the authors present as a natural consequence",
        ),
        (
            r"\bright revolutionary theory\b",
            "revolutionary theory the authors regard as correct",
        ),
        (
            r"\badvanced proletarian party of a new kind\b",
            "Bolshevik party, described by the authors as advanced and proletarian",
        ),
        (
            r"\bproletarian party of a new kind\b",
            "Bolshevik party, described by the authors as a proletarian party of a new kind",
        ),
        (
            r"\bscientific socialism\b",
            "Marxist socialism, termed scientific socialism by the authors",
        ),
        (
            r"\bLenin's method\b",
            "the method attributed to Lenin by Soviet historiography",
        ),
        (
            r"\bthe victory in Russia of Marxism\b",
            "the emergence of Marxism as the dominant framework within Russian revolutionary socialism, as interpreted by the authors",
        ),
        (
            r"\bthe victory of Marxism\b",
            "the emergence of Marxism as a dominant revolutionary framework, as interpreted by the authors",
        ),
        (
            r"\blead the masses to victory\b",
            "lead mass politics, in the authors' account, toward revolutionary victory",
        ),
        (
            r"\bleading revolutionary forces\b",
            "forces the authors identify as leading the revolutionary movement",
        ),
        (
            r"\bwas not a fortuitous event\b",
            "is presented by the authors as not being a fortuitous event",
        ),
        (
            r"\bThere can be no doubt that\b",
            "The authors argue that",
        ),
        (
            r"\bLet us emphasize once again that\b",
            "The authors emphasize that",
        ),
        (
            r"\bIt was Lenin who formulated\b",
            "The authors credit Lenin with formulating",
        ),
        (
            r"\ban erroneous approach dictated primarily by a non-scientific, anti-communist aim\b",
            "an approach the authors reject as anti-communist and methodologically flawed",
        ),
        (
            r"\bsole scientific theory of revolutionary socialism\b",
            "central Marxist framework of revolutionary socialism, in the authors' account",
        ),
        (
            r"\bunder the banner of Marxism\b",
            "within a Marxist political framework, in the authors' account",
        ),
        (
            r"\bto orientate themselves correctly\b",
            "to orient themselves effectively, in the authors' view,",
        ),
    )
    updated = segment
    for pattern, replacement in replacements:
        updated = re.sub(pattern, replacement, updated, flags=re.I)
    return updated


def add_framework_context_once(text: str, categories: Iterable[str]) -> str:
    category_set = set(categories)
    if "MARXIST_LENINIST_FRAME" not in category_set and "CLASS_STRUGGLE_REDUCTION" not in category_set:
        return text
    if "bourgeois development" in text:
        return text.replace(
            "bourgeois development",
            "bourgeois development (the authors' Marxist term for capitalist development)",
            1,
        )
    if "bourgeois system (which" in text:
        return text.replace(
            "bourgeois system (which",
            "capitalist system (described by the authors in Marxist terminology as the bourgeois system, which",
            1,
        )
    if "bourgeois system." in text:
        return text.replace(
            "bourgeois system.",
            "capitalist system (described by the authors in Marxist terminology as the bourgeois system).",
            1,
        )
    if "bourgeois system," in text:
        return text.replace(
            "bourgeois system,",
            "capitalist system (described by the authors in Marxist terminology as the bourgeois system),",
            1,
        )
    if "bourgeois system" in text:
        return text.replace(
            "bourgeois system",
            "capitalist system (described by the authors in Marxist terminology as the bourgeois system)",
            1,
        )
    return text


def neutralize_block(block: str, categories: list[str]) -> tuple[str, str, bool]:
    if is_heading(block):
        return block, "preserved_heading", False
    if "BIBLIOGRAPHIC_OR_NOTE" in categories:
        return block, "preserved_note_or_index", False
    if "PRIMARY_QUOTE" in categories and len(categories) <= 2:
        return block, "preserved_primary_quote", False

    ideology_categories = {
        "MARXIST_LENINIST_FRAME",
        "TELEOLOGICAL_HISTORY",
        "SOVIET_POLEMIC",
        "ANTI_WESTERN_POLEMIC",
        "CLASS_STRUGGLE_REDUCTION",
        "LENINIST_VALIDATION",
        "BOLSHEVIK_TRIUMPHALISM",
    }
    if not ideology_categories.intersection(categories):
        return block, "left_unchanged", False

    segments = split_protected_quote_segments(block)
    neutralized = "".join(
        segment if protected else apply_segment_replacements(segment)
        for segment, protected in segments
    )
    neutralized = add_framework_context_once(neutralized, categories)

    high_risk = {
        "TELEOLOGICAL_HISTORY",
        "BOLSHEVIK_TRIUMPHALISM",
        "ANTI_WESTERN_POLEMIC",
    }.intersection(categories)
    if high_risk and neutralized == block:
        neutralized = (
            "[Editorial note: This paragraph reflects Soviet Marxist-Leninist historiography; "
            "its factual claims should be read as part of that interpretive framework.] "
            + block
        )
        return neutralized, "tagged_editorial_note", True
    if neutralized != block:
        return neutralized, "neutralized_by_attribution_or_qualification", bool(high_risk)
    return block, "flagged_left_unchanged", bool(high_risk)


def confidence_for(categories: list[str], action: str) -> str:
    if action in {"preserved_note_or_index", "preserved_primary_quote", "preserved_heading"}:
        return "high"
    high_risk = {"TELEOLOGICAL_HISTORY", "BOLSHEVIK_TRIUMPHALISM", "ANTI_WESTERN_POLEMIC"}
    if high_risk.intersection(categories):
        return "medium"
    if len(categories) >= 3:
        return "medium"
    return "high"


def update_chapter_context(block: str, current_chapter: str) -> str:
    stripped = block.strip()
    if stripped == "INTRODUCTION":
        return "INTRODUCTION"
    if stripped == "In Place of a Conclusion":
        return "In Place of a Conclusion"
    if stripped == "Name Index":
        return "Name Index"
    if stripped == "Subject Index":
        return "Subject Index"
    chapter_match = CHAPTER_RE.match(stripped)
    if chapter_match:
        return chapter_match.group(1).rstrip(".")
    return current_chapter


def process_text(text: str) -> tuple[str, list[ParagraphRecord]]:
    blocks = split_blocks(text)
    output_blocks: list[str] = []
    records: list[ParagraphRecord] = []
    current_chapter = "Frontmatter"

    for index, block in enumerate(blocks, start=1):
        current_chapter = update_chapter_context(block, current_chapter)
        categories = classify_block(block, current_chapter, index)
        neutralized, action, review_from_action = neutralize_block(block, categories)
        needs_review = review_from_action or action in {"tagged_editorial_note", "flagged_left_unchanged"}
        if "PRIMARY_QUOTE" in categories:
            needs_review = False if action == "preserved_primary_quote" else needs_review
        records.append(
            ParagraphRecord(
                paragraph_id=index,
                chapter=current_chapter,
                original=block,
                neutralized=neutralized,
                categories=categories,
                action_taken=action,
                confidence=confidence_for(categories, action),
                needs_manual_review=needs_review,
            )
        )
        output_blocks.append(neutralized)

    neutralized_text = LEGAL_NOTICE + "\n\n" + "\n\n".join(output_blocks).strip() + "\n"
    return neutralized_text, records


def validate_output(original: str, neutralized: str) -> ValidationResult:
    failures: list[str] = []
    warnings: list[str] = []
    required_markers = [
        "Progress Publishers",
        "Translated from the Russian by Cynthia Carlile",
        "ISBN",
        "CONTENTS",
        "INTRODUCTION",
        "In Place of a Conclusion",
        "Name Index",
        "Subject Index",
    ]
    for marker in required_markers:
        if marker not in neutralized:
            failures.append(f"Missing structural/frontmatter marker: {marker}")

    for number in (
        "One",
        "Two",
        "Three",
        "Four",
        "Five",
        "Six",
        "Seven",
        "Eight",
        "Nine",
        "Ten",
        "Eleven",
        "Twelve",
        "Thirteen",
        "Fourteen",
    ):
        marker = f"Chapter {number}"
        if marker not in neutralized:
            failures.append(f"Missing chapter marker: {marker}")

    original_tokens = estimated_token_count(original)
    neutralized_tokens = estimated_token_count(neutralized)
    ratio = neutralized_tokens / original_tokens if original_tokens else 0
    if not 0.95 <= ratio <= 1.05:
        failures.append(f"Token ratio outside 95%-105%: {ratio:.4f}")

    inserted_polemics = (
        "communism was evil",
        "anti-communist conclusion",
        "free-market superiority",
        "totalitarian by nature",
    )
    lowered = neutralized.lower()
    if any(phrase in lowered for phrase in inserted_polemics):
        failures.append("Potential anti-communist editorializing detected.")

    compact_neutralized = re.sub(r"\s+", " ", neutralized)
    if "The Russian Revolutionary Tradition" not in compact_neutralized:
        warnings.append("Title marker not found after neutralization.")

    recommendation = "FAIL" if failures else "PASS_WITH_REVIEW"
    return ValidationResult(recommendation=recommendation, failures=failures, warnings=warnings)


def build_audit(original: str, neutralized: str, records: list[ParagraphRecord], validation: ValidationResult) -> dict:
    category_counter: Counter[str] = Counter()
    chapter_counter: defaultdict[str, int] = defaultdict(int)
    for record in records:
        category_counter.update(record.categories)
        if record.needs_manual_review:
            chapter_counter[record.chapter] += 1

    original_tokens = estimated_token_count(original)
    neutralized_tokens = estimated_token_count(neutralized)
    token_delta = ((neutralized_tokens - original_tokens) / original_tokens * 100) if original_tokens else 0.0
    neutralized_count = sum(
        1
        for record in records
        if record.action_taken in {"neutralized_by_attribution_or_qualification", "tagged_editorial_note"}
    )
    flagged_count = sum(
        1
        for record in records
        if any(
            category
            in {
                "MARXIST_LENINIST_FRAME",
                "TELEOLOGICAL_HISTORY",
                "SOVIET_POLEMIC",
                "ANTI_WESTERN_POLEMIC",
                "CLASS_STRUGGLE_REDUCTION",
                "LENINIST_VALIDATION",
                "BOLSHEVIK_TRIUMPHALISM",
            }
            for category in record.categories
        )
    )
    return {
        "original_file": str(INPUT_PATH.relative_to(REPO_ROOT)),
        "neutralized_file": str(NEUTRALIZED_PATH.relative_to(REPO_ROOT)),
        "original_char_count": len(original),
        "neutralized_char_count": len(neutralized),
        "original_word_count": word_count(original),
        "neutralized_word_count": word_count(neutralized),
        "estimated_original_tokens": original_tokens,
        "estimated_neutralized_tokens": neutralized_tokens,
        "token_delta_percent": round(token_delta, 4),
        "total_paragraphs": len(records),
        "paragraphs_flagged": flagged_count,
        "paragraphs_neutralized": neutralized_count,
        "paragraphs_left_unchanged": len(records) - neutralized_count,
        "top_bias_categories": category_counter.most_common(12),
        "high_risk_sections": sorted(chapter_counter.items(), key=lambda item: (-item[1], item[0]))[:12],
        "quality_gate": {
            "failures": validation.failures,
            "warnings": validation.warnings,
        },
        "recommendation": validation.recommendation,
    }


def write_bias_map(records: list[ParagraphRecord]) -> None:
    with BIAS_MAP_PATH.open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=[
                "paragraph_id",
                "chapter",
                "original_excerpt",
                "bias_categories",
                "action_taken",
                "neutralized_excerpt",
                "confidence",
                "needs_manual_review",
            ],
        )
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "paragraph_id": record.paragraph_id,
                    "chapter": record.chapter,
                    "original_excerpt": excerpt(record.original),
                    "bias_categories": "|".join(record.categories),
                    "action_taken": record.action_taken,
                    "neutralized_excerpt": excerpt(record.neutralized),
                    "confidence": record.confidence,
                    "needs_manual_review": "yes" if record.needs_manual_review else "no",
                }
            )


def build_editorial_notes(audit: dict, records: list[ParagraphRecord]) -> str:
    high_review = [record for record in records if record.needs_manual_review][:20]
    useful_flagged = [
        record
        for record in records
        if "FACTUAL_NARRATIVE" not in record.categories
        and any(cat in record.categories for cat in ("MARXIST_LENINIST_FRAME", "CLASS_STRUGGLE_REDUCTION"))
    ][:12]
    top_categories = "\n".join(
        f"- {category}: {count}" for category, count in audit["top_bias_categories"]
    )
    high_sections = "\n".join(
        f"- {chapter}: {count} review flags" for chapter, count in audit["high_risk_sections"]
    )
    review_lines = "\n".join(
        f"- Paragraph {record.paragraph_id} ({record.chapter}): {excerpt(record.original, 180)}"
        for record in high_review
    )
    useful_lines = "\n".join(
        f"- Paragraph {record.paragraph_id} ({record.chapter}): {excerpt(record.original, 180)}"
        for record in useful_flagged
    )
    return f"""# Neutralized Study Edition - Editorial Notes

## Legal/Editorial Status

This output is an internal research/study transformation of a copyrighted 1988 translated Soviet-era secondary source. It is not a publishable edition, not an original public-domain text, and should not be distributed as a replacement for the source.

## Main Ideological Patterns Found

- Soviet Marxist-Leninist framing presents Russian revolutionary history as a sequence culminating in Marxism, Bolshevism, and socialist revolution.
- Teleological language appears around inevitability, historical necessity, natural consequence, and the idea of a path toward communism.
- Western historiography is sometimes described polemically through anti-communist or bourgeois labels.
- Class categories are frequently used as the primary explanatory framework for historical development.
- Lenin and Bolshevism are often validated as resolving earlier theoretical and organizational problems.

## Bias Category Counts

{top_categories}

## Chapters With Strongest Ideological Framing

{high_sections}

## Repeated Soviet Historiographical Assumptions

- Revolutionary movements are frequently evaluated by how far they anticipate Marxism or Bolshevism.
- The October Revolution is presented as the culmination of long-term Russian historical development.
- Marxist terminology such as bourgeois development, scientific socialism, and proletarian leadership is treated as analytic vocabulary rather than one historiographical framework among others.
- Liberal, Western, and anti-communist scholarship is often treated as politically tendentious.

## Passages Requiring Manual Review

{review_lines}

## Factually Useful Passages Despite Ideological Framing

{useful_lines}

## Use Warnings

- Use this file for research, comparison, citation extraction, and historiographical mapping only.
- Do not cite the neutralized study edition as if it were the authors' original wording.
- When citing, verify against the original 1988 translation or a reliable bibliographic copy.
- Do not use this as a publishable edition or as public-domain source text.
- Review all paragraphs marked `needs_manual_review=yes` in the bias map before relying on interpretation.

## Quality Gate

- Recommendation: {audit["recommendation"]}
- Token delta: {audit["token_delta_percent"]}%
- Paragraphs processed: {audit["total_paragraphs"]}
- Paragraphs flagged: {audit["paragraphs_flagged"]}
- Paragraphs neutralized: {audit["paragraphs_neutralized"]}
"""


def main() -> int:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"Input not found: {INPUT_PATH}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    original = INPUT_PATH.read_text(encoding="utf-8")
    neutralized, records = process_text(original)
    validation = validate_output(original, neutralized)
    audit = build_audit(original, neutralized, records, validation)

    NEUTRALIZED_PATH.write_text(neutralized, encoding="utf-8")
    AUDIT_PATH.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_bias_map(records)
    EDITORIAL_NOTES_PATH.write_text(build_editorial_notes(audit, records), encoding="utf-8")

    print("Files created:")
    print(f"- {NEUTRALIZED_PATH}")
    print(f"- {AUDIT_PATH}")
    print(f"- {BIAS_MAP_PATH}")
    print(f"- {EDITORIAL_NOTES_PATH}")
    print(f"Paragraphs processed: {audit['total_paragraphs']}")
    print(f"Paragraphs neutralized: {audit['paragraphs_neutralized']}")
    print(f"Token delta: {audit['token_delta_percent']}%")
    print(f"Recommendation: {audit['recommendation']}")
    if validation.failures:
        print("Failures:")
        for failure in validation.failures:
            print(f"- {failure}")
    return 1 if audit["recommendation"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
