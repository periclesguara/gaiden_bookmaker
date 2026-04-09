from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from gaiden.application.pipeline.ingest import extract_text_from_html
from gaiden.application.pipeline.normalization import normalize_text_v2, roman_to_int
from gaiden.domain.editorial.collections import validate_contiguous_order, validate_item_count, validate_no_duplicates

from . import collections_storage


_BOOK_LABELS = {
    1: "BOOK ONE",
    2: "BOOK TWO",
    3: "BOOK THREE",
    4: "BOOK FOUR",
    5: "BOOK FIVE",
    6: "BOOK SIX",
    7: "BOOK SEVEN",
    8: "BOOK EIGHT",
    9: "BOOK NINE",
    10: "BOOK TEN",
}


@dataclass(frozen=True)
class PreparedItemResult:
    order_index: int
    upload_path: Path
    prepared_path: Path
    upload_sha256: str
    prepared_sha256: str


@dataclass(frozen=True)
class NormalizedItemResult:
    order_index: int
    prepared_path: Path
    normalized_path: Path
    normalized_sha256: str


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _collapse_blank(lines: list[str]) -> list[str]:
    out: list[str] = []
    prev_blank = False
    for line in lines:
        if not line.strip():
            if prev_blank:
                continue
            prev_blank = True
            out.append("")
            continue
        prev_blank = False
        out.append(line.strip())
    return out


def _normalize_heading_roman(line: str) -> str:
    match = re.match(
        r"^(?P<label>(?:chapter|book|part|section|adventure))\s+(?P<number>[IVXLCDM]+)(?P<suffix>\b.*)$",
        line.strip(),
        re.IGNORECASE,
    )
    if not match:
        return line.strip()
    number = roman_to_int(match.group("number"))
    if number is None:
        return line.strip()
    return f"{match.group('label').upper()} {number}{match.group('suffix')}".strip()


def mechanically_prepare_collection_text(raw_text: str) -> str:
    lines = [line.strip() for line in raw_text.splitlines()]
    filtered: list[str] = []
    skipping_contents = False
    for line in lines:
        lower = line.lower()
        if not line:
            filtered.append("")
            continue
        if "*** start of" in lower or "*** end of" in lower:
            continue
        if "project gutenberg" in lower and ("ebook" in lower or "license" in lower):
            continue
        if lower.startswith(("title:", "author:", "release date:", "language:", "ebook #", "produced by:")):
            continue
        if lower in {"contents", "table of contents", "index"}:
            skipping_contents = True
            continue
        if skipping_contents:
            if re.match(r"^(chapter|book|part|section|adventure)\b", line, re.IGNORECASE):
                skipping_contents = False
            elif len(line.split()) <= 12:
                continue
            else:
                skipping_contents = False
        if "end of the project gutenberg" in lower or "full project gutenberg license" in lower:
            break
        if re.fullmatch(r"\d+", line):
            continue
        filtered.append(_normalize_heading_roman(line))
    prepared_lines = _collapse_blank(filtered)
    prepared_text = "\n".join(prepared_lines).strip()
    if not prepared_text:
        raise ValueError("Mechanical preparation produced an empty text.")
    return prepared_text + "\n"


def prepare_collection_items(collection, items) -> list[PreparedItemResult]:
    active_items = [item for item in items if item.is_active]
    validate_item_count(len(active_items))
    validate_contiguous_order([item.order_index for item in active_items])
    validate_no_duplicates([(item.author_name.strip().lower(), item.work_title.strip().lower()) for item in active_items])
    collections_storage.ensure_collection_layout(collection.code, collection.language)

    results: list[PreparedItemResult] = []
    for item in sorted(active_items, key=lambda obj: obj.order_index):
        upload_path = Path(item.source_original_path)
        if not upload_path.exists():
            raise FileNotFoundError(f"Missing upload for collection item {item.order_index}: {upload_path}")
        extracted_text = extract_text_from_html(upload_path)
        if not extracted_text:
            raise ValueError(f"Could not extract text from collection item {item.order_index}: {upload_path}")
        prepared_text = mechanically_prepare_collection_text(extracted_text)
        prepared_path = collections_storage.item_prepared_path(collection.code, collection.language, item.order_index)
        _ensure_parent(prepared_path)
        prepared_path.write_text(prepared_text, encoding="utf-8")
        results.append(
            PreparedItemResult(
                order_index=item.order_index,
                upload_path=upload_path,
                prepared_path=prepared_path,
                upload_sha256=_sha256_path(upload_path),
                prepared_sha256=_sha256_text(prepared_text),
            )
        )
    return results


def normalize_collection_items(collection, items) -> list[NormalizedItemResult]:
    active_items = [item for item in items if item.is_active]
    validate_item_count(len(active_items))
    validate_contiguous_order([item.order_index for item in active_items])
    validate_no_duplicates([(item.author_name.strip().lower(), item.work_title.strip().lower()) for item in active_items])
    collections_storage.ensure_collection_layout(collection.code, collection.language)

    results: list[NormalizedItemResult] = []
    for item in sorted(active_items, key=lambda obj: obj.order_index):
        prepared_path = collections_storage.item_prepared_path(collection.code, collection.language, item.order_index)
        if not prepared_path.exists():
            raise FileNotFoundError(f"Prepared item missing for collection item {item.order_index}: {prepared_path}")
        prepared_text = prepared_path.read_text(encoding="utf-8")
        normalized_text = normalize_text_v2(prepared_text)
        normalized_path = collections_storage.item_normalized_path(collection.code, collection.language, item.order_index)
        _ensure_parent(normalized_path)
        normalized_path.write_text(normalized_text.strip() + "\n", encoding="utf-8")
        results.append(
            NormalizedItemResult(
                order_index=item.order_index,
                prepared_path=prepared_path,
                normalized_path=normalized_path,
                normalized_sha256=_sha256_text(normalized_text),
            )
        )
    return results


def merge_collection_items(collection, items) -> Path:
    active_items = [item for item in items if item.is_active]
    validate_item_count(len(active_items))
    validate_contiguous_order([item.order_index for item in active_items])
    collections_storage.ensure_collection_layout(collection.code, collection.language)

    parts: list[str] = []
    for item in sorted(active_items, key=lambda obj: obj.order_index):
        normalized_path = collections_storage.item_normalized_path(collection.code, collection.language, item.order_index)
        if not normalized_path.exists():
            raise FileNotFoundError(f"Normalized item missing for collection item {item.order_index}: {normalized_path}")
        parts.append(_BOOK_LABELS.get(item.order_index, f"BOOK {item.order_index}"))
        parts.append("")
        parts.append(item.work_title.strip())
        parts.append("")
        parts.append(normalized_path.read_text(encoding="utf-8").strip())
        parts.append("")
        parts.append("")

    merged_text = "\n".join(parts).strip() + "\n"
    merged_path = collections_storage.merged_source_path(collection.code, collection.language)
    _ensure_parent(merged_path)
    merged_path.write_text(merged_text, encoding="utf-8")
    return merged_path


def write_manifest(collection, items, merged_path: Path | None = None) -> Path:
    collections_storage.ensure_collection_layout(collection.code, collection.language)
    manifest = {
        "collection_code": collection.code,
        "title": collection.title,
        "subtitle": collection.subtitle,
        "language": collection.language,
        "collection_kind": collection.collection_kind,
        "status": getattr(collection, "status", ""),
        "item_count": collection.item_count,
        "items": [],
        "merged_final": {
            "path": str(merged_path) if merged_path else None,
            "sha256": _sha256_path(merged_path) if merged_path and merged_path.exists() else None,
        },
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    for item in sorted([item for item in items if item.is_active], key=lambda obj: obj.order_index):
        prepared = collections_storage.item_prepared_path(collection.code, collection.language, item.order_index)
        normalized = collections_storage.item_normalized_path(collection.code, collection.language, item.order_index)
        manifest["items"].append(
            {
                "order_index": item.order_index,
                "author_name": item.author_name,
                "work_title": item.work_title,
                "source_filename": item.source_filename,
                "source_original_path": item.source_original_path,
                "uploaded_at": item.uploaded_at.isoformat() if getattr(item, "uploaded_at", None) else None,
                "upload_status": getattr(item, "upload_status", ""),
                "prep_status": getattr(item, "prep_status", ""),
                "normalize_status": getattr(item, "normalize_status", ""),
                "merge_status": getattr(item, "merge_status", ""),
                "prepared_output": {
                    "path": str(prepared) if prepared.exists() else None,
                    "sha256": _sha256_path(prepared) if prepared.exists() else None,
                },
                "normalized_output": {
                    "path": str(normalized) if normalized.exists() else None,
                    "sha256": _sha256_path(normalized) if normalized.exists() else None,
                },
            }
        )

    path = collections_storage.manifest_path(collection.code, collection.language)
    _ensure_parent(path)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
