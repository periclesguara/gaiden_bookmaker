"""Stable, local-only extraction of source provenance metadata.

The extractor intentionally records bibliographic and acquisition facts only.
It never returns manuscript text, volatile download statistics, reading levels,
or technical modification timestamps.
"""

from __future__ import annotations

import hashlib
import html
import re
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree


SCHEMA_VERSION = "source_provenance_v1"
EDITABLE_FIELDS = (
    "original_title",
    "source_author",
    "original_publication_year",
    "original_publication_basis",
    "source_platform",
    "source_identifier",
    "source_url",
    "source_release_date",
    "source_credits",
    "rights",
    "source_language",
    "subjects",
)
READ_ONLY_FIELDS = ("source_filename", "source_sha256")

_PG_ID_RE = re.compile(
    r"(?:gutenberg\.org/(?:ebooks|cache/epub)/|ebook(?:\s+number)?\s*#?\s*)(\d{2,8})",
    re.IGNORECASE,
)
_COPYRIGHT_YEAR_RE = re.compile(
    r"(?:copyright|©|copr\.)[^\n]{0,80}?\b((?:17|18|19|20)\d{2})\b",
    re.IGNORECASE,
)
_ORIGINAL_PUBLICATION_RE = re.compile(
    r"(?:first|originally)\s+publish(?:ed|ing)[^\n]{0,80}?\b((?:17|18|19|20)\d{2})\b",
    re.IGNORECASE,
)
_HEADER_RE = re.compile(
    r"^(title|author|language|credits?|rights?|subjects?|release\s+date)\s*:\s*(.+?)\s*$",
    re.IGNORECASE,
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean(value: str) -> str:
    value = html.unescape(value or "")
    return re.sub(r"\s+", " ", value).strip(" \t\r\n;,")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _person_name(value: str) -> str:
    value = _clean(value)
    if value.count(",") == 1:
        family, given = (_clean(part) for part in value.split(",", 1))
        if family and given:
            return f"{given} {family}"
    return value


def _iso_date(value: str) -> str:
    value = _clean(value)
    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", value)
    if match:
        return match.group(0)
    value = re.sub(r"(?i)\b(?:first\s+)?release(?:d)?(?:\s+date)?\s*:\s*", "", value)
    textual = re.search(r"\b([A-Za-z]+\s+\d{1,2},\s+\d{4})\b", value)
    if textual:
        value = textual.group(1)
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%B %Y"):
        try:
            parsed = datetime.strptime(value.strip(" ."), fmt)
            if fmt == "%B %Y":
                return parsed.strftime("%Y-%m")
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _first(values: Iterable[str]) -> str:
    return next((_clean(value) for value in values if _clean(value)), "")


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _clean(raw)
        key = value.casefold()
        if value and key not in seen:
            result.append(value)
            seen.add(key)
    return result


def _base_record(filename: str, data: bytes) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_filename": Path(filename).name,
        "source_sha256": sha256_bytes(data),
    }


def _publication_fact(text: str) -> tuple[int | None, str]:
    match = _COPYRIGHT_YEAR_RE.search(text)
    if match:
        return int(match.group(1)), "copyright_notice"
    match = _ORIGINAL_PUBLICATION_RE.search(text)
    if match:
        return int(match.group(1)), "original_publication_statement"
    return None, ""


def _source_identity(text: str) -> tuple[str, str, str]:
    match = _PG_ID_RE.search(text)
    if match:
        identifier = match.group(1)
        return "Project Gutenberg", identifier, f"https://www.gutenberg.org/ebooks/{identifier}"
    return "", "", ""


def _header_values(text: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for line in text.splitlines():
        match = _HEADER_RE.match(line.strip())
        if not match:
            continue
        key = re.sub(r"\s+", "_", match.group(1).lower())
        values.setdefault(key, []).append(_clean(match.group(2)))
    return values


def _merge_text_facts(record: dict, text: str) -> None:
    headers = _header_values(text)
    if not record.get("original_title"):
        record["original_title"] = _first(headers.get("title", []))
    if not record.get("source_author"):
        record["source_author"] = _person_name(_first(headers.get("author", [])))
    if not record.get("source_language"):
        record["source_language"] = _first(headers.get("language", []))
    if not record.get("source_credits"):
        record["source_credits"] = _first(headers.get("credits", []) + headers.get("credit", []))
    if not record.get("rights"):
        record["rights"] = _first(headers.get("rights", []) + headers.get("right", []))
    if not record.get("subjects"):
        record["subjects"] = _unique(headers.get("subjects", []) + headers.get("subject", []))

    if not record.get("source_release_date"):
        release = _first(headers.get("release_date", []))
        record["source_release_date"] = _iso_date(release)

    if not record.get("original_publication_year"):
        year, basis = _publication_fact(text)
        if year:
            record["original_publication_year"] = year
            record["original_publication_basis"] = basis

    if not record.get("source_identifier"):
        platform, identifier, url = _source_identity(text)
        record["source_platform"] = platform
        record["source_identifier"] = identifier
        record["source_url"] = url


def _safe_xml(data: bytes) -> ElementTree.Element | None:
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return None


def _epub_package_path(files: dict[str, bytes]) -> str:
    container = _safe_xml(files.get("META-INF/container.xml", b""))
    if container is not None:
        for node in container.iter():
            if _local_name(node.tag) == "rootfile" and node.attrib.get("full-path"):
                return node.attrib["full-path"]
    return next((name for name in files if name.lower().endswith(".opf")), "")


def _epub_text(files: dict[str, bytes]) -> str:
    parts: list[str] = []
    for name, payload in files.items():
        if not name.lower().endswith((".xhtml", ".html", ".htm", ".xml", ".opf")):
            continue
        root = _safe_xml(payload)
        if root is not None:
            text = "".join(root.itertext())
            parts.append("\n".join(_clean(line) for line in text.splitlines() if _clean(line)))
        else:
            decoded = payload.decode("utf-8", errors="replace")
            parts.append(_clean(re.sub(r"<[^>]+>", " ", decoded)))
    return "\n".join(parts)


def _extract_epub(data: bytes, filename: str) -> dict:
    record = _base_record(filename, data)
    with zipfile.ZipFile(BytesIO(data)) as archive:
        files = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}

    package_path = _epub_package_path(files)
    package = _safe_xml(files.get(package_path, b""))
    metadata: dict[str, list[str]] = {}
    identifiers: list[str] = []
    if package is not None:
        for node in package.iter():
            name = _local_name(node.tag)
            value = _clean(node.text or "")
            if name == "meta" and (node.attrib.get("property") or "").lower() == "dcterms:modified":
                continue
            if name in {"title", "creator", "language", "subject", "rights", "contributor", "date"} and value:
                metadata.setdefault(name, []).append(value)
            elif name == "identifier" and value:
                identifiers.append(value)

    record.update(
        {
            "original_title": _first(metadata.get("title", [])),
            "source_author": _person_name(_first(metadata.get("creator", []))),
            "source_language": _first(metadata.get("language", [])),
            "subjects": _unique(metadata.get("subject", [])),
            "rights": _first(metadata.get("rights", [])),
            "source_credits": "; ".join(_unique(metadata.get("contributor", []))),
            "source_release_date": _iso_date(_first(metadata.get("date", []))),
        }
    )
    combined = "\n".join(identifiers) + "\n" + _epub_text(files)
    _merge_text_facts(record, combined)
    return record


def _extract_text(data: bytes, filename: str) -> dict:
    record = _base_record(filename, data)
    text = data.decode("utf-8-sig", errors="replace")
    _merge_text_facts(record, text)
    return record


def _prune(record: dict) -> dict:
    allowed = {"schema_version", *EDITABLE_FIELDS, *READ_ONLY_FIELDS, "extraction_warnings"}
    result = {}
    for key, value in record.items():
        if key not in allowed or value in (None, "", []):
            continue
        result[key] = value
    return result


def extract_source_provenance_bytes(data: bytes, filename: str) -> dict:
    """Extract provenance without changing or retaining the supplied bytes."""

    base = _base_record(filename, data)
    try:
        suffix = Path(filename).suffix.lower()
        if suffix == ".epub":
            record = _extract_epub(data, filename)
        elif suffix in {".txt", ".md", ".markdown"}:
            record = _extract_text(data, filename)
        else:
            raise ValueError(f"Unsupported source format: {suffix or 'unknown'}")
    except Exception as exc:
        base["extraction_warnings"] = [str(exc)]
        return _prune(base)

    missing = [field for field in ("original_title", "source_author") if not record.get(field)]
    if missing:
        record["extraction_warnings"] = [
            "Review required: metadata not found for " + ", ".join(missing)
        ]
    return _prune(record)


def extract_source_provenance(path: str | Path) -> dict:
    source = Path(path)
    return extract_source_provenance_bytes(source.read_bytes(), source.name)
