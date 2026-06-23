#!/usr/bin/env python3
"""Create a pre-Marxist research corpus by cutting closing ML chapters."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = REPO_ROOT / "data/neutralized/the_russian_revolutionary_tradition_neutralized_bias_only_final_v02.txt"
OUTPUT_DIR = REPO_ROOT / "data/corpus"
CORPUS_PATH = OUTPUT_DIR / "the_russian_revolutionary_tradition_pre_marxist_research_corpus.txt"
REPORT_PATH = OUTPUT_DIR / "the_russian_revolutionary_tradition_pre_marxist_cut_report.json"
ARCHIVE_PATH = OUTPUT_DIR / "the_russian_revolutionary_tradition_removed_sections_archive.txt"

INTERNAL_NOTE = (
    "[Internal corpus note: Chapters Twelve, Thirteen, Fourteen, and In Place of a Conclusion "
    "were excluded from this working corpus because they primarily frame the pre-Marxist "
    "revolutionary tradition through a Soviet Marxist-Leninist conclusion. The removed sections "
    "are preserved separately for audit and reference.]"
)

WORD_RE = re.compile(r"\b[\w’'-]+\b", re.UNICODE)


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def estimated_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def find_line_starts(text: str, heading: str) -> list[int]:
    pattern = re.compile(rf"(?im)^{re.escape(heading)}\.?\s*$")
    return [match.start() for match in pattern.finditer(text)]


def find_line_start(text: str, heading: str, *, start: int = 0) -> int:
    pattern = re.compile(rf"(?im)^{re.escape(heading)}\.?\s*$")
    match = pattern.search(text, start)
    if not match:
        raise ValueError(f"Heading not found: {heading}")
    return match.start()


def line_heading_present(text: str, heading: str) -> bool:
    return re.search(rf"(?im)^{re.escape(heading)}\.?\s*$", text) is not None


def remove_cut_entries_from_contents(text: str, body_cut_start: int) -> tuple[str, str]:
    starts = [pos for pos in find_line_starts(text, "Chapter Twelve") if pos < body_cut_start]
    if not starts:
        return text[:body_cut_start], ""
    toc_cut_start = starts[0]
    toc_cut_end = find_line_start(text, "Name Index", start=toc_cut_start)
    kept = text[:toc_cut_start].rstrip() + "\n\n" + text[toc_cut_end:body_cut_start].lstrip()
    removed_toc = text[toc_cut_start:toc_cut_end].strip()
    return kept, removed_toc


def create_corpus(text: str) -> tuple[str, str, list[str], list[str]]:
    chapter_twelve_starts = find_line_starts(text, "Chapter Twelve")
    if not chapter_twelve_starts:
        raise ValueError("Heading not found: Chapter Twelve")
    cut_start = chapter_twelve_starts[-1]
    kept_before_cut, removed_toc = remove_cut_entries_from_contents(text, cut_start)
    index_start = find_line_start(text, "NAME INDEX", start=cut_start)
    subject_start = find_line_start(text, "Subject Index", start=index_start)

    removed_body = text[cut_start:index_start].strip()
    removed_parts = []
    if removed_toc:
        removed_parts.append("[Removed contents entries]\n\n" + removed_toc)
    removed_parts.append(removed_body)
    removed = "\n\n".join(removed_parts).strip() + "\n"
    indexes = text[index_start:].strip()
    corpus = kept_before_cut + "\n\n" + INTERNAL_NOTE + "\n\n" + indexes + "\n"

    preserved_sections = [
        "Frontmatter",
        "Contents",
        "Introduction",
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
        "Name Index",
        "Subject Index",
    ]
    removed_sections = [
        "Chapter Twelve",
        "Chapter Thirteen",
        "Chapter Fourteen",
        "In Place of a Conclusion",
    ]
    if subject_start <= index_start:
        raise ValueError("Subject Index was not safely separable after Name Index.")
    return corpus, removed, preserved_sections, removed_sections


def build_report(original: str, corpus: str, removed: str, preserved: list[str], removed_sections: list[str]) -> dict:
    required_present = all(
        line_heading_present(corpus, marker)
        for marker in (
            "INTRODUCTION",
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
        )
    )
    removed_absent = all(not line_heading_present(corpus, marker) for marker in removed_sections)
    archive_ok = all(line_heading_present(removed, marker) for marker in removed_sections)
    name_index_preserved = line_heading_present(corpus, "Name Index")
    subject_index_preserved = line_heading_present(corpus, "Subject Index")
    all_required = bool(required_present and removed_absent and archive_ok and name_index_preserved and subject_index_preserved)
    recommendation = "PASS_WITH_REVIEW" if all_required else "FAIL"
    return {
        "input_file": str(INPUT_PATH.relative_to(REPO_ROOT)),
        "output_file": str(CORPUS_PATH.relative_to(REPO_ROOT)),
        "removed_sections_archive": str(ARCHIVE_PATH.relative_to(REPO_ROOT)),
        "cut_start_heading": "Chapter Twelve / THE MARXIST SOLUTION OF THE PROBLEM",
        "cut_end_heading": "In Place of a Conclusion",
        "preserved_sections": preserved,
        "removed_sections": removed_sections,
        "original_char_count": len(original),
        "corpus_char_count": len(corpus),
        "removed_char_count": len(removed),
        "original_word_count": word_count(original),
        "corpus_word_count": word_count(corpus),
        "removed_word_count": word_count(removed),
        "estimated_original_tokens": estimated_tokens(original),
        "estimated_corpus_tokens": estimated_tokens(corpus),
        "estimated_removed_tokens": estimated_tokens(removed),
        "all_required_sections_present": all_required,
        "name_index_preserved": name_index_preserved,
        "subject_index_preserved": subject_index_preserved,
        "removed_sections_archived": archive_ok,
        "removed_sections_absent_from_corpus": removed_absent,
        "no_rewrite_performed": True,
        "recommendation": recommendation,
    }


def main() -> int:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"Input not found: {INPUT_PATH}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    original = INPUT_PATH.read_text(encoding="utf-8")
    corpus, removed, preserved, removed_sections = create_corpus(original)
    report = build_report(original, corpus, removed, preserved, removed_sections)

    CORPUS_PATH.write_text(corpus, encoding="utf-8")
    ARCHIVE_PATH.write_text(removed, encoding="utf-8")
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Corpus: {CORPUS_PATH}")
    print(f"Removed archive: {ARCHIVE_PATH}")
    print(f"Report: {REPORT_PATH}")
    print(f"Original chars: {report['original_char_count']}")
    print(f"Corpus chars: {report['corpus_char_count']}")
    print(f"Removed chars: {report['removed_char_count']}")
    print(f"Recommendation: {report['recommendation']}")
    return 0 if report["recommendation"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
