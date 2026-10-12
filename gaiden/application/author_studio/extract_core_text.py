from __future__ import annotations

import re

from gaiden.domain.author_studio.entities import CoreTextResult
from gaiden.domain.author_studio.enums import SourceProvider

_START_MARKERS = (
    r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*",
    r"START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*",
)
_END_MARKERS = (
    r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*",
    r"END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*",
    r"End of Project Gutenberg.*",
)
_EXTERNAL_LINES = re.compile(
    r"(?i)^(?:isbn\b|copyright\b|all rights reserved\b|transcriber(?:'s)? notes?\b|"
    r"internet archive\b|standard ebooks\b|project gutenberg\b|cover image\b|image\b|illustration\b)"
)
_NARRATIVE_HEADING = re.compile(
    r"(?i)^(?:chapter|part|book|canto|act|scene|prologue|epilogue|letter|story|tale)\b|^(?:[IVXLCDM]+|\d+)[\s.:—-]"
)
_FRONT_HEADING = re.compile(
    r"(?i)^(?:preface|introduction|foreword|dedication|copyright|credits?|about (?:the )?author|"
    r"translator(?:'s)? note|editor(?:'s)? note|contents|table of contents|glossary|bibliography)\s*$"
)


def identify_source_provider(text: str) -> SourceProvider:
    lowered = text.lower()
    if "project gutenberg" in lowered:
        return SourceProvider.PROJECT_GUTENBERG
    if "standard ebooks" in lowered or "standardebooks.org" in lowered:
        return SourceProvider.STANDARD_EBOOKS
    if "internet archive" in lowered or "archive.org" in lowered:
        return SourceProvider.INTERNET_ARCHIVE
    return SourceProvider.UNKNOWN


def apply_core_text_policy(text: str) -> CoreTextResult:
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        return CoreTextResult("", True, 0.0)
    removed: list[str] = []
    for marker in _START_MARKERS:
        match = re.search(marker, value, flags=re.IGNORECASE)
        if match:
            value = value[match.end():].lstrip()
            removed.append("project_gutenberg_header")
            break
    for marker in _END_MARKERS:
        match = re.search(marker, value, flags=re.IGNORECASE)
        if match:
            value = value[:match.start()].rstrip()
            removed.append("project_gutenberg_footer")
            break

    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", value)
    value = re.sub(r"(?is)<(?:img|figure)\b[^>]*>.*?</figure>|<img\b[^>]*?/?>", "", value)
    lines = [line.strip() for line in value.splitlines()]
    narrative_indexes = [index for index, line in enumerate(lines) if _NARRATIVE_HEADING.match(line)]
    first_front = next((index for index, line in enumerate(lines) if _FRONT_HEADING.match(line)), None)
    if first_front is not None:
        next_body = next((index for index in narrative_indexes if index > first_front), None)
        if next_body is not None:
            title_lines = [line for line in lines[:first_front] if line and not _EXTERNAL_LINES.match(line)]
            lines = title_lines[:2] + lines[next_body:]
            removed.append("front_matter")

    cleaned: list[str] = []
    previous = None
    for line in lines:
        if _EXTERNAL_LINES.match(line):
            removed.append("external_metadata")
            continue
        if re.fullmatch(r"(?:page\s+)?\d+", line, flags=re.IGNORECASE):
            continue
        if line == previous and line:
            continue
        cleaned.append(line)
        previous = line
    value = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned)).strip()
    word_count = len(re.findall(r"\b\w+\b", value, flags=re.UNICODE))
    has_structure = any(_NARRATIVE_HEADING.match(line) for line in value.splitlines())
    needs_review = word_count < 100 or not has_structure
    confidence = 0.9 if word_count >= 100 and has_structure else (0.55 if word_count >= 100 else 0.25)
    return CoreTextResult(value, needs_review, confidence, tuple(dict.fromkeys(removed)))
