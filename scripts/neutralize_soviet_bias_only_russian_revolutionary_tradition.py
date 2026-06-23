#!/usr/bin/env python3
"""Neutralize Marxist-Leninist historiographical framing only.

This is not an OCR cleanup pass. The script preserves the input's paragraph
separators and only applies targeted historiographical attribution/qualification
to detected ideological framing.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = REPO_ROOT / "data/cleaned/the_russian_revolutionary_tradition_cleaned.txt"
OUTPUT_DIR = REPO_ROOT / "data/neutralized"
NEUTRALIZED_PATH = OUTPUT_DIR / "the_russian_revolutionary_tradition_neutralized_bias_only.txt"
REPORT_PATH = OUTPUT_DIR / "the_russian_revolutionary_tradition_bias_only_report.json"
MAP_PATH = OUTPUT_DIR / "the_russian_revolutionary_tradition_bias_only_map.csv"

WORD_RE = re.compile(r"\b[\w’'-]+\b", re.UNICODE)
CHAPTER_RE = re.compile(r"^Chapter\s+(One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|Eleven|Twelve|Thirteen|Fourteen)\.?\s*$", re.I)
STRUCTURAL_RE = re.compile(
    r"^(?:The Russian|Revolutionary Tradition|CONTENTS|INTRODUCTION|Introduction|"
    r"In Place of a Conclusion|Name Index|Subject Index|Chapter\s+"
    r"(?:One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|Eleven|Twelve|Thirteen|Fourteen)\.?)$",
    re.I,
)
NOTE_OR_INDEX_RE = re.compile(
    r"^(?:\d{1,3}\s*(?:[A-Z][A-Za-z. -]*|V\.?I\.?|M\.?N\.?|See:|Cf\.|Ibid\.|Marx|Engels|Lenin|Herzen|Plekhanov)|"
    r"[A-Z][A-Za-z' .()/-]+,\s+\d)"
)
PRIMARY_QUOTE_RE = re.compile(r"^\s*[\"']")


CATEGORY_PATTERNS: dict[str, tuple[str, ...]] = {
    "ML_TELEOLOGY": (
        r"path leading to communism",
        r"path to socialism",
        r"culminated .* victory .* Marxism",
        r"from .* to scientific socialism",
        r"under the banner of Marxism",
    ),
    "BOLSHEVIK_TRIUMPHALISM": (
        r"Bolshevism, having freed itself",
        r"social revolution .* victory",
        r"Bolsheviks .* lead the masses to victory",
        r"lead the masses to victory",
        r"purposeful activity of its leading revolutionary forces",
        r"proletarian party of a new kind",
        r"Bolshevik party .* future destiny",
    ),
    "LENINIST_VALIDATION": (
        r"Lenin's method",
        r"Lenin defined precisely",
        r"It was Lenin who formulated",
        r"Lenin was able to discern",
        r"Leninist-Bolsheviks",
        r"right revolutionary theory",
    ),
    "SCIENTIFIC_SOCIALISM_CLAIM": (
        r"scientific socialism",
        r"sole scientific theory",
        r"scientific theory of revolutionary socialism",
        r"founders of scientific socialism",
    ),
    "WESTERN_HISTORIOGRAPHY_DISMISSAL": (
        r"anti-communist orientation",
        r"anti-communist aim",
        r"bourgeois historiography",
        r"Sovietologists",
        r"Western literature",
        r"Western scholars",
    ),
    "CLASS_STRUGGLE_REDUCTION": (
        r"class struggle",
        r"class forces",
        r"class relations",
        r"proletariat",
        r"bourgeoisie",
        r"exploiter",
    ),
    "REVOLUTIONARY_HEROIZATION": (
        r"bold heroes",
        r"cause of revolution",
        r"joy of victory",
        r"gave their lives",
        r"titanic effort",
    ),
    "SOVIET_SUPERIORITY_CLAIM": (
        r"Soviet historical science enormous progress",
        r"Soviet researchers gradually mastered",
        r"Soviet historical research in no way excludes but presupposes",
        r"convincing refutation .* Soviet historical science",
    ),
    "DETERMINISTIC_HISTORY": (
        r"historical necessity",
        r"natural consequence",
        r"not a fortuitous event",
        r"inevitable",
        r"inevitably",
        r"doomed to perish",
        r"guaranteed by",
    ),
}

COMPILED_CATEGORY_PATTERNS = {
    category: tuple(re.compile(pattern, re.I | re.S) for pattern in patterns)
    for category, patterns in CATEGORY_PATTERNS.items()
}


@dataclass
class ParagraphMapRow:
    paragraph_id: int
    chapter: str
    bias_category: str
    original_excerpt: str
    neutralized_excerpt: str
    action_taken: str
    confidence: str
    needs_manual_review: bool


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def estimated_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def excerpt(text: str, limit: int = 260) -> str:
    compact = re.sub(r"\s+", " ", text.strip())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def split_preserving_separators(text: str) -> list[str]:
    return re.split(r"(\n\s*\n)", text)


def is_heading(block: str) -> bool:
    stripped = block.strip()
    if not stripped or "\n" in stripped:
        return False
    if STRUCTURAL_RE.match(stripped):
        return True
    if len(stripped) <= 100 and stripped.isupper() and any(char.isalpha() for char in stripped):
        return True
    if len(stripped) <= 110 and re.match(r"^[A-Z][A-Za-z' .:;-]+$", stripped):
        words = stripped.split()
        if len(words) <= 10:
            uppercase_starts = sum(1 for word in words if word[:1].isupper())
            return uppercase_starts / max(len(words), 1) >= 0.7
    return False


def is_note_or_index(block: str, chapter: str) -> bool:
    stripped = block.strip()
    if chapter in {"Name Index", "Subject Index"}:
        return True
    if NOTE_OR_INDEX_RE.match(stripped):
        return True
    if stripped.startswith(("ISBN", "© Progress Publishers", "Printed in the Union")):
        return True
    if stripped[:2].isdigit() and any(marker in stripped for marker in ("Collected Works", "Moscow,", "Vol.", "p.", "pp.", "in Russian")):
        return True
    return False


def is_primary_quote(block: str) -> bool:
    stripped = block.strip()
    if PRIMARY_QUOTE_RE.match(stripped):
        return True
    quote_count = stripped.count('"') + stripped.count("'")
    return len(stripped) > 120 and quote_count >= 10 and quote_count / len(stripped) > 0.05


def current_chapter_for(block: str, current: str) -> str:
    stripped = block.strip()
    if stripped == "INTRODUCTION":
        return "INTRODUCTION"
    if stripped == "In Place of a Conclusion":
        return "In Place of a Conclusion"
    if stripped == "Name Index":
        return "Name Index"
    if stripped == "Subject Index":
        return "Subject Index"
    match = CHAPTER_RE.match(stripped)
    if match:
        return f"Chapter {match.group(1).capitalize()}"
    return current


def classify(block: str, chapter: str) -> list[str]:
    if is_heading(block) or is_note_or_index(block, chapter) or is_primary_quote(block):
        return ["FACTUAL_NO_CHANGE"]
    categories: list[str] = []
    for category, patterns in COMPILED_CATEGORY_PATTERNS.items():
        if any(pattern.search(block) for pattern in patterns):
            categories.append(category)
    return categories or ["FACTUAL_NO_CHANGE"]


def split_quote_segments(text: str) -> list[tuple[str, bool]]:
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


def apply_bias_only_replacements(segment: str) -> str:
    replacements: tuple[tuple[str, str], ...] = (
        (
            r"The Russian revolutionary tradition had its origins at the end of the 18th century\. Its history is the history of the amazing transformation of the seemingly not very significant poetic exercises of one man, the revolutionary nobleman Radishchev, into the three popular revolutions of the 20th century which radically changed the face of Russia and, in many ways, that of the world\. Such a transformation required truly titanic effort: the work of great minds and the deeds of bold heroes, a campaign to enlighten and rally the political vanguard, and work to arouse the broad masses, reflection on and correction of errors\. Travelling this route, revolutionaries experienced the bitterness of defeat and the joy of victory; thousands of lives were given to the cause of revolution\.",
            "The Russian revolutionary tradition had its origins at the end of the 18th century. The authors trace its development from the writings of the nobleman Radishchev to the popular revolutions of the 20th century, which radically changed Russia and influenced world history. This development involved intellectual work, political organization, efforts to mobilize broader publics, debate over strategy, defeats, repression, exile, and death.",
        ),
        (
            r"The October Revolution of 1917, coming as the conclusion of a great historical struggle, took the country along the path of socialist development\.",
            "The authors interpret the October Revolution of 1917 as the conclusion of a long historical struggle and as the beginning of socialist development in Russia.",
        ),
        (
            r"Soviet researchers gradually mastered Lenin's method of studying the Russian liberation movement, and used it to examine",
            "Soviet researchers increasingly used the interpretive method associated with Lenin in Soviet historiography to examine",
        ),
        (
            r"anti-communist orientation",
            "anti-communist, liberal, or non-Marxist orientation",
        ),
        (
            r"There can be no doubt that",
            "The authors argue that",
        ),
        (
            r"If the October Revolution of 1917 was prepared for by the Russian revolutionary movement of the 19th century, it was so only in the sense that Bolshevism, having freed itself from the legacy of immature revolutionarism, assimilated the best traditions of Russian revolutionary democracy:",
            "If the October Revolution of 1917 was prepared for by the Russian revolutionary movement of the 19th century, the authors argue that this was because Bolshevism assimilated traditions of Russian revolutionary democracy while moving beyond what they describe as immature revolutionism:",
        ),
        (
            r"legacy of immature revolutionarism",
            "legacy of earlier revolutionary politics, characterized by the authors as immature revolutionism",
        ),
        (
            r"the consolidation within the country of the bourgeois system \(which differed considerably from the emergence of Western capitalism and was marked by a certain underdevelopment\)",
            "the consolidation within the country of the capitalist system, described in the authors' Marxist terminology as the bourgeois system and characterized by them as differing considerably from Western capitalism and as marked by underdevelopment,",
        ),
        (
            r"Russia adopted the path of socialist development, the path leading to communism\.",
            "Soviet historiography described post-1917 Russia as entering a socialist path.",
        ),
        (
            r"the theory of scientific socialism based on the actual economic laws governing the development of the new capitalist order",
            "Marxist theory, described by the authors as scientific socialism, and presented by them as based on the economic laws governing the new capitalist order",
        ),
        (
            r"the movement of the country along the path to socialism had become a historical necessity",
            "the authors present the country's movement toward socialism as historically necessary",
        ),
        (
            r"The realization of this necessity was assisted in Russia by the existence of an advanced proletarian party of a new kind, armed with a revolutionary theory\.",
            "In the authors' interpretation, this development was assisted in Russia by a Bolshevik party they describe as an advanced proletarian party of a new kind, armed with revolutionary theory.",
        ),
        (
            r"finally culminated at the beginning of the 20th century with the victory in Russia of Marxism\.",
            "led at the beginning of the 20th century to the emergence of Marxism as a dominant framework within Russian revolutionary socialism, in the authors' account.",
        ),
        (
            r"The right revolutionary theory enabled the Bolsheviks to orientate themselves correctly in a highly complex situation, to formulate precise and meaningful slogans of struggle, and to lead the masses to victory\.",
            "In the authors' interpretation, Marxist theory enabled the Bolsheviks to orient themselves in a complex situation, formulate slogans of struggle, and lead the revolutionary process.",
        ),
        (
            r"Taken as a whole, the socialist revolution in Russia in 1917 was not a fortuitous event, was not the result of forceful intervention in history by a handful of Bolsheviks \(an assertion frequently met with in Western literature\)\. On the contrary, it appears as the natural consequence of a long process of national historical development, of the purposeful activity of its leading revolutionary forces\.",
            "The authors reject interpretations of the 1917 socialist revolution as a fortuitous event or as the result of intervention by a small group of Bolsheviks. They interpret it instead as the outcome of a long process of national historical development and of the activity of revolutionary forces.",
        ),
        (
            r"The Marxist basis underiying Soviet historical research in no way excludes but presupposes diverse points of view, the clash of differing concepts\.",
            "The authors argue that the Marxist basis underlying Soviet historical research did not exclude diverse points of view or the clash of differing concepts.",
        ),
        (
            r"openly anti-communist point of view",
            "openly anti-communist point of view",
        ),
        (
            r"an erroneous approach dictated primarily by a non-scientific, anti-communist aim",
            "an approach the authors reject as anti-communist and methodologically flawed",
        ),
        (
            r"the transition from utopian socialism to scientific socialism in Russia in the 19th century was the natural consequence of a long and painful search which Lenin defined precisely as mastery of the revolutionary process",
            "the authors interpret the transition from utopian socialism to Marxist socialism in Russia in the 19th century as the result of a long search, one that Lenin described as mastery of the revolutionary process",
        ),
        (
            r"together with the 'correct revolutionary theory'",
            "together with what the authors call the 'correct revolutionary theory'",
        ),
        (
            r"which largely contributed to its victory",
            "which, in the authors' interpretation, contributed to its victory",
        ),
        (
            r"The proletarian party of a new kind, founded by Lenin in 1903, was based on",
            "The Bolshevik party founded by Lenin in 1903 is presented by the authors as being based on",
        ),
        (
            r"the founders of scientific socialism were confident",
            "Marx and Engels, described by the authors as founders of scientific socialism, were confident",
        ),
        (
            r"as the sole scientific theory of revolutionary socialism",
            "as the central Marxist theory of revolutionary socialism, in the authors' account",
        ),
        (
            r"All of this was evidence of a decisive shift",
            "The authors interpret this as evidence of a decisive shift",
        ),
        (
            r"had now become natural and inevitable",
            "is presented by the authors as having become increasingly likely",
        ),
        (
            r"it stood firm under the banner of Marxism",
            "it operated within a Marxist political framework, in the authors' account",
        ),
        (
            r"the creation of the organizational basis of a proletarian party of a new kind by the Leninist-Bolsheviks at the Second Congress of the RSDLP \(1903\) was of no little significance for the future destiny of Russia",
            "the authors attach major significance to the creation of the organizational basis of the Bolshevik party at the Second Congress of the RSDLP (1903)",
        ),
        (
            r"which were corrupted by parliamentary legalism and tolerant of opportunism",
            "which the authors criticize as shaped by parliamentary legalism and tolerant of opportunism",
        ),
        (
            r"scientific socialism, which became the possession of the whole of humanity",
            "Marxist socialism, described by the authors as scientific socialism",
        ),
    )
    updated = segment
    for pattern, replacement in replacements:
        updated = re.sub(pattern, replacement, updated, flags=re.I | re.S)
    return updated


def neutralize_paragraph(block: str, categories: list[str], chapter: str) -> tuple[str, str, bool, str]:
    if categories == ["FACTUAL_NO_CHANGE"] or is_heading(block) or is_note_or_index(block, chapter) or is_primary_quote(block):
        return block, "no_change", False, "high"

    protected = split_quote_segments(block)
    neutralized = "".join(segment if is_quote else apply_bias_only_replacements(segment) for segment, is_quote in protected)
    changed = neutralized != block

    high_risk_categories = {
        "ML_TELEOLOGY",
        "BOLSHEVIK_TRIUMPHALISM",
        "LENINIST_VALIDATION",
        "SCIENTIFIC_SOCIALISM_CLAIM",
        "WESTERN_HISTORIOGRAPHY_DISMISSAL",
        "SOVIET_SUPERIORITY_CLAIM",
        "DETERMINISTIC_HISTORY",
    }
    if changed:
        review = bool(high_risk_categories.intersection(categories))
        confidence = "medium" if review else "high"
        return neutralized, "neutralized_by_attribution", review, confidence

    if high_risk_categories.intersection(categories):
        return block, "flagged_no_automatic_change", True, "medium"
    return block, "flagged_no_automatic_change", False, "high"


def build_outputs(original: str) -> tuple[str, list[ParagraphMapRow]]:
    parts = split_preserving_separators(original)
    rebuilt: list[str] = []
    rows: list[ParagraphMapRow] = []
    paragraph_id = 0
    chapter = "Frontmatter"

    for part in parts:
        if part == "":
            continue
        if re.fullmatch(r"\n\s*\n", part):
            rebuilt.append(part)
            continue
        paragraph_id += 1
        chapter = current_chapter_for(part, chapter)
        categories = classify(part, chapter)
        neutralized, action, review, confidence = neutralize_paragraph(part, categories, chapter)
        rows.append(
            ParagraphMapRow(
                paragraph_id=paragraph_id,
                chapter=chapter,
                bias_category="|".join(categories),
                original_excerpt=excerpt(part),
                neutralized_excerpt=excerpt(neutralized),
                action_taken=action,
                confidence=confidence,
                needs_manual_review=review,
            )
        )
        rebuilt.append(neutralized)

    return "".join(rebuilt), rows


def validate(original: str, neutralized: str) -> tuple[str, list[str]]:
    failures: list[str] = []
    required = [
        "Progress Publishers",
        "Translated from the Russian by Cynthia Carlile",
        "ISBN",
        "CONTENTS",
        "INTRODUCTION",
        "In Place of a Conclusion",
        "Name Index",
        "Subject Index",
    ]
    for marker in required:
        if marker not in neutralized:
            failures.append(f"missing marker: {marker}")
    for chapter in (
        "Chapter One",
        "Chapter Two",
        "Chapter Three",
        "Chapter Four",
        "Chapter Five",
        "Chapter Six",
        "Chapter Seven",
        "Chapter Eight",
        "Chapter Nine",
        "Chapter Ten",
        "Chapter Eleven",
        "Chapter Twelve",
        "Chapter Thirteen",
        "Chapter Fourteen",
    ):
        if chapter not in neutralized:
            failures.append(f"missing chapter: {chapter}")

    original_tokens = estimated_tokens(original)
    neutralized_tokens = estimated_tokens(neutralized)
    ratio = neutralized_tokens / original_tokens if original_tokens else 0
    if not 0.95 <= ratio <= 1.05:
        failures.append(f"token ratio outside 95%-105%: {ratio:.4f}")

    if "communism was evil" in neutralized.lower() or "capitalism was superior" in neutralized.lower():
        failures.append("possible anti-communist counter-propaganda inserted")

    return ("FAIL" if failures else "PASS_WITH_REVIEW"), failures


def build_report(original: str, neutralized: str, rows: list[ParagraphMapRow], recommendation: str, failures: list[str]) -> dict:
    category_counter: Counter[str] = Counter()
    chapter_counter: defaultdict[str, int] = defaultdict(int)
    examples: list[dict[str, str | int]] = []

    changed = [row for row in rows if row.action_taken == "neutralized_by_attribution"]
    for row in rows:
        for category in row.bias_category.split("|"):
            category_counter[category] += 1
        if row.needs_manual_review:
            chapter_counter[row.chapter] += 1
    for row in changed[:12]:
        examples.append(
            {
                "paragraph_id": row.paragraph_id,
                "chapter": row.chapter,
                "category": row.bias_category,
                "original_excerpt": row.original_excerpt,
                "neutralized_excerpt": row.neutralized_excerpt,
            }
        )

    original_tokens = estimated_tokens(original)
    neutralized_tokens = estimated_tokens(neutralized)
    return {
        "input_file": str(INPUT_PATH.relative_to(REPO_ROOT)),
        "neutralized_file": str(NEUTRALIZED_PATH.relative_to(REPO_ROOT)),
        "bias_map_file": str(MAP_PATH.relative_to(REPO_ROOT)),
        "total_paragraphs": len(rows),
        "changed_paragraphs": len(changed),
        "unchanged_paragraphs": len(rows) - len(changed),
        "changes_by_category": category_counter.most_common(),
        "high_risk_chapters": sorted(chapter_counter.items(), key=lambda item: (-item[1], item[0]))[:12],
        "examples_of_major_neutralizations": examples,
        "original_char_count": len(original),
        "neutralized_char_count": len(neutralized),
        "original_word_count": word_count(original),
        "neutralized_word_count": word_count(neutralized),
        "estimated_original_tokens": original_tokens,
        "estimated_neutralized_tokens": neutralized_tokens,
        "token_delta_percent": round(((neutralized_tokens - original_tokens) / original_tokens * 100), 4) if original_tokens else 0.0,
        "quality_gate_failures": failures,
        "recommendation": recommendation,
        "notes": [
            "Bias-only pass: no OCR cleanup, paragraph joining, spelling normalization, or punctuation cleanup was performed.",
            "Primary quotes, notes, bibliographic references, and indexes are preserved by rule.",
            "Rows marked needs_manual_review=yes contain ideological framing that was high-risk or not safely transformable by rules.",
        ],
    }


def write_map(rows: list[ParagraphMapRow]) -> None:
    with MAP_PATH.open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=[
                "paragraph_id",
                "chapter",
                "bias_category",
                "original_excerpt",
                "neutralized_excerpt",
                "action_taken",
                "confidence",
                "needs_manual_review",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "paragraph_id": row.paragraph_id,
                    "chapter": row.chapter,
                    "bias_category": row.bias_category,
                    "original_excerpt": row.original_excerpt,
                    "neutralized_excerpt": row.neutralized_excerpt,
                    "action_taken": row.action_taken,
                    "confidence": row.confidence,
                    "needs_manual_review": "yes" if row.needs_manual_review else "no",
                }
            )


def main() -> int:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"Input not found: {INPUT_PATH}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    original = INPUT_PATH.read_text(encoding="utf-8")
    neutralized, rows = build_outputs(original)
    recommendation, failures = validate(original, neutralized)
    report = build_report(original, neutralized, rows, recommendation, failures)

    NEUTRALIZED_PATH.write_text(neutralized, encoding="utf-8")
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_map(rows)

    print(f"Neutralized text: {NEUTRALIZED_PATH}")
    print(f"Report: {REPORT_PATH}")
    print(f"Bias map: {MAP_PATH}")
    print(f"Paragraphs processed: {report['total_paragraphs']}")
    print(f"Paragraphs changed: {report['changed_paragraphs']}")
    print(f"Token delta: {report['token_delta_percent']}%")
    print(f"Recommendation: {recommendation}")
    if failures:
        print("Failures:")
        for failure in failures:
            print(f"- {failure}")
    return 1 if recommendation == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
