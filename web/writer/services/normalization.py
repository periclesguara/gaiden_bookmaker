from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from ..models import SourceDocument

GUTENBERG_START = re.compile(
    r"(?im)^\s*\*{0,3}\s*START OF (?:THIS|THE) PROJECT GUTENBERG EBOOK.*$"
)
GUTENBERG_END = re.compile(
    r"(?im)^\s*\*{0,3}\s*END OF (?:THIS|THE) PROJECT GUTENBERG EBOOK.*$"
)
NARRATIVE_HEADING = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:BOOK|PART|CHAPTER)\s+"
    r"(?:[IVXLCDM]+|\d+|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN)\b.*$"
    r"|^\s*(?:#{1,6}\s*)?(?:PROLOGUE|PREFACE|INTRODUCTION)\s*$"
)
BACK_MATTER = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:TRANSCRIBER['’]S NOTES?|"
    r"ABOUT (?:STANDARD EBOOKS|PROJECT GUTENBERG)|COLOPHON|UNCOPYRIGHT|LICENSE)\s*$"
)
YAML_FRONTMATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
HTML_TAG = re.compile(r"<[^>]+>")
MULTI_BLANK = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class NormalizedText:
    text: str
    provider: str
    report: dict


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def detect_provider(text: str) -> str:
    sample = text[:12000].casefold()
    if "project gutenberg" in sample:
        return "PROJECT_GUTENBERG"
    if "standard ebooks" in sample or "standardebooks.org" in sample:
        return "STANDARD_EBOOKS"
    if "internet archive" in sample or "archive.org" in sample or "full text of" in sample:
        return "INTERNET_ARCHIVE"
    return "UNKNOWN"


def normalize_text(raw: str) -> NormalizedText:
    text = raw.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if "\x00" in text:
        raise ValueError("binary/NUL input is not supported")
    original_length = len(text)
    provider = detect_provider(text)
    rules: list[str] = []

    yaml_match = YAML_FRONTMATTER.match(text)
    if yaml_match:
        text = text[yaml_match.end():]
        rules.append("yaml-frontmatter")

    start = GUTENBERG_START.search(text)
    if start:
        text = text[start.end():]
        rules.append("gutenberg-start-marker")
    end = GUTENBERG_END.search(text)
    if end:
        text = text[:end.start()]
        rules.append("gutenberg-end-marker")

    if provider in {"PROJECT_GUTENBERG", "STANDARD_EBOOKS", "INTERNET_ARCHIVE"}:
        first_heading = NARRATIVE_HEADING.search(text)
        if first_heading and first_heading.start() < max(25000, len(text) // 3):
            text = text[first_heading.start():]
            rules.append("provider-frontmatter-before-first-narrative-unit")
        back = None
        for match in BACK_MATTER.finditer(text):
            if match.start() > len(text) * 0.65:
                back = match
                break
        if back:
            text = text[:back.start()]
            rules.append("provider-backmatter")

    if "<html" in text[:500].casefold() or "<body" in text[:2000].casefold():
        text = HTML_TAG.sub("", text)
        rules.append("html-tags")

    text = MULTI_BLANK.sub("\n\n", text).strip() + "\n"
    if len(text) < 500:
        raise ValueError("normalization produced less than 500 characters; review the source")
    return NormalizedText(
        text=text,
        provider=provider,
        report={
            "rules": rules,
            "original_characters": original_length,
            "normalized_characters": len(text),
            "removed_characters": max(0, original_length - len(text)),
            "preserved_epilogue_policy": "Narrative EPILOGUE headings are preserved.",
        },
    )


def writer_storage_root() -> Path:
    configured = os.environ.get("GAIDEN_WRITER_STORAGE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    storage = os.environ.get("GAIDEN_STORAGE_ROOT", "").strip()
    if storage:
        return (Path(storage).expanduser().resolve() / "writer")
    return (Path(settings.BASE_DIR).parent / "data" / "writer").resolve()


def normalize_document(document: SourceDocument) -> SourceDocument:
    configured_source = Path(document.source_path).expanduser()
    if configured_source.is_symlink():
        raise ValueError("symlink sources are forbidden")
    source = configured_source.resolve(strict=True)
    if source.suffix.casefold() not in {".txt", ".md"}:
        raise ValueError("only direct .txt and .md source files are supported")
    raw = source.read_text(encoding="utf-8", errors="strict")
    result = normalize_text(raw)
    source_sha = sha256_text(raw)
    normalized_sha = sha256_text(result.text)
    destination = writer_storage_root() / "normalized" / f"{normalized_sha}.txt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256_text(destination.read_text(encoding="utf-8")) != normalized_sha:
            raise ValueError("normalized destination exists with different content")
    else:
        fd, temporary_name = tempfile.mkstemp(dir=destination.parent, prefix=".normalize-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(result.text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, destination)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise
    document.source_sha256 = source_sha
    document.normalized_path = str(destination)
    document.normalized_sha256 = normalized_sha
    document.provider = result.provider
    document.normalization_report = result.report
    document.status = SourceDocument.Status.NORMALIZED
    document.normalized_at = timezone.now()
    document.error_message = ""
    document.save()
    return document
