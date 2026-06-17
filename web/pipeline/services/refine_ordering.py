from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import canonical_merge


REFINE_MANIFEST_NAME = "refine_manifest.json"
MERGE_REPORT_NAME = "merge_report.json"
REFINE_OUTPUT_SUFFIX = ".refine.txt"
REFINE_META_SUFFIX = ".refine.json"


@dataclass(frozen=True)
class RefineChunk:
    book_id: str
    lang: str
    stage: str
    run_id: str
    source_chunk_id: str
    chapter_index: int
    chunk_index: int
    source_path: str
    source_sha256: str
    output_filename: str
    source_chunks_manifest_path: str = ""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metadata_path_for_output(output_path: Path) -> Path:
    if output_path.name.endswith(REFINE_OUTPUT_SUFFIX):
        return output_path.with_name(output_path.name[: -len(REFINE_OUTPUT_SUFFIX)] + REFINE_META_SUFFIX)
    return output_path.with_suffix(output_path.suffix + ".json")


def canonical_output_filename(chapter_index: int, chunk_index: int) -> str:
    return f"ch_{chapter_index:03d}_chunk_{chunk_index:03d}{REFINE_OUTPUT_SUFFIX}"


def _source_chunk_id(filename: str) -> str:
    return Path(filename).stem


def _ordered_split_manifest_entries(
    *,
    manifest_path: Path,
    source_dir: Path,
    book_id: str,
    lang: str,
    stage: str,
    run_id: str,
) -> list[RefineChunk]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    chapters = payload.get("chapters") or []
    entries: list[RefineChunk] = []

    for chapter in sorted(chapters, key=lambda item: int(item.get("index") or 0)):
        chapter_index = int(chapter.get("index") or 0)
        parts = chapter.get("parts") or []
        for part in sorted(parts, key=lambda item: int(item.get("index") or 0)):
            chunk_index = int(part.get("index") or 0)
            filename = str(part.get("filename") or "").strip()
            if not filename:
                continue
            source_path = source_dir / filename
            if not source_path.exists():
                raise FileNotFoundError(f"Manifest source chunk is missing: {source_path}")
            entries.append(
                RefineChunk(
                    book_id=book_id,
                    lang=lang,
                    stage=stage,
                    run_id=run_id,
                    source_chunk_id=_source_chunk_id(filename),
                    chapter_index=chapter_index,
                    chunk_index=chunk_index,
                    source_path=str(source_path),
                    source_sha256=sha256_file(source_path),
                    output_filename=canonical_output_filename(chapter_index, chunk_index),
                    source_chunks_manifest_path=str(manifest_path),
                )
            )
    return entries


def load_refine_chunks_from_manifest(
    *,
    manifest_path: Path,
    source_dir: Path,
    book_id: str,
    lang: str,
    run_id: str,
    stage: str = "refine",
) -> list[RefineChunk]:
    if not manifest_path.exists():
        raise FileNotFoundError(f"Refine ordering manifest not found: {manifest_path}")
    chunks = _ordered_split_manifest_entries(
        manifest_path=manifest_path,
        source_dir=source_dir,
        book_id=book_id,
        lang=lang,
        stage=stage,
        run_id=run_id,
    )
    if not chunks:
        raise ValueError(f"No refine chunks found in manifest: {manifest_path}")
    return chunks


def write_refine_output_metadata(output_path: Path, chunk: RefineChunk, report: dict[str, Any] | None = None) -> Path:
    meta_path = metadata_path_for_output(output_path)
    payload = asdict(chunk)
    payload.update(
        {
            "output_path": str(output_path),
            "output_sha256": sha256_file(output_path) if output_path.exists() else None,
            "agent_report_status": (report or {}).get("status"),
            "audit_path": (report or {}).get("audit_path"),
        }
    )
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta_path


def _load_output_metadata(meta_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _expected_by_id(chunks: list[RefineChunk]) -> dict[str, RefineChunk]:
    return {chunk.source_chunk_id: chunk for chunk in chunks}


def _sort_key(chunk: RefineChunk) -> tuple[int, int, str]:
    return (chunk.chapter_index, chunk.chunk_index, chunk.source_chunk_id)


def _has_book_heading(text: str, roman: str | None = None) -> bool:
    for line in text.splitlines():
        match = BOOK_HEADING_RE.match(line)
        if not match:
            continue
        if roman is None:
            return True
        if match.group(1).upper() == roman.upper():
            return True
    return False


def _starts_with_chapter_one(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return bool(re.match(r"^(?:#{1,6}\s*)?Chapter\s+1\b", stripped, re.IGNORECASE))
    return False


def _should_infer_initial_book_one(chunks: list[RefineChunk]) -> bool:
    if not chunks:
        return False
    ordered = sorted(chunks, key=_sort_key)
    first_text = Path(ordered[0].source_path).read_text(encoding="utf-8")
    if _has_book_heading(first_text) or not _starts_with_chapter_one(first_text):
        return False
    return any(_has_book_heading(Path(chunk.source_path).read_text(encoding="utf-8"), "II") for chunk in ordered[1:])


def validate_refine_run(run_dir: Path, chunks: list[RefineChunk]) -> dict[str, Any]:
    expected_by_id = _expected_by_id(chunks)
    output_files = sorted(run_dir.glob(f"*{REFINE_OUTPUT_SUFFIX}"))
    seen_by_id: dict[str, list[Path]] = {}
    extra_outputs: list[str] = []
    empty_outputs: list[str] = []
    metadata_mismatches: list[dict[str, str]] = []
    sha_mismatches: list[dict[str, str]] = []
    ordered_outputs: list[dict[str, Any]] = []
    expected_run_id = chunks[0].run_id if chunks else run_dir.name
    expected_book_id = chunks[0].book_id if chunks else ""
    expected_lang = chunks[0].lang if chunks else ""
    expected_stage = chunks[0].stage if chunks else "refine"

    for output_path in output_files:
        meta_path = metadata_path_for_output(output_path)
        metadata = _load_output_metadata(meta_path)
        source_chunk_id = str(metadata.get("source_chunk_id") or "").strip()
        output_text = output_path.read_text(encoding="utf-8", errors="replace")
        if not output_text.strip():
            empty_outputs.append(output_path.name)
        if not source_chunk_id:
            extra_outputs.append(output_path.name)
            continue
        seen_by_id.setdefault(source_chunk_id, []).append(output_path)
        expected = expected_by_id.get(source_chunk_id)
        if expected is None:
            extra_outputs.append(output_path.name)
            continue
        for field, expected_value in (
            ("run_id", expected_run_id),
            ("book_id", expected_book_id),
            ("lang", expected_lang),
            ("stage", expected_stage),
        ):
            observed = str(metadata.get(field) or "")
            if observed != str(expected_value):
                metadata_mismatches.append(
                    {
                        "source_chunk_id": source_chunk_id,
                        "path": str(output_path),
                        "field": field,
                        "expected": str(expected_value),
                        "observed": observed,
                    }
                )
        recorded_output_sha = str(metadata.get("output_sha256") or "")
        current_output_sha = sha256_file(output_path)
        if recorded_output_sha and recorded_output_sha != current_output_sha:
            sha_mismatches.append(
                {
                    "source_chunk_id": source_chunk_id,
                    "expected_sha256": current_output_sha,
                    "recorded_sha256": recorded_output_sha,
                    "current_sha256": current_output_sha,
                    "kind": "output",
                    "path": str(output_path),
                }
            )
        current_source = Path(str(metadata.get("source_path") or expected.source_path))
        current_sha = sha256_file(current_source) if current_source.exists() else ""
        recorded_sha = str(metadata.get("source_sha256") or "")
        if current_sha != expected.source_sha256 or recorded_sha != expected.source_sha256:
            sha_mismatches.append(
                {
                    "source_chunk_id": source_chunk_id,
                    "expected_sha256": expected.source_sha256,
                    "recorded_sha256": recorded_sha,
                    "current_sha256": current_sha,
                    "kind": "source",
                    "path": str(current_source),
                }
            )

    missing_outputs = [
        chunk.source_chunk_id
        for chunk in sorted(chunks, key=_sort_key)
        if chunk.source_chunk_id not in seen_by_id
    ]
    duplicates = [
        {"source_chunk_id": source_chunk_id, "outputs": [path.name for path in paths]}
        for source_chunk_id, paths in sorted(seen_by_id.items())
        if len(paths) > 1
    ]

    previous_key: tuple[int, int, str] | None = None
    order_errors: list[str] = []
    for chunk in sorted(chunks, key=_sort_key):
        key = _sort_key(chunk)
        if previous_key is not None and key < previous_key:
            order_errors.append(chunk.source_chunk_id)
        previous_key = key
        output_path = run_dir / chunk.output_filename
        if output_path.exists():
            ordered_outputs.append(
                {
                    "source_chunk_id": chunk.source_chunk_id,
                    "chapter_index": chunk.chapter_index,
                    "chunk_index": chunk.chunk_index,
                    "source_path": chunk.source_path,
                    "source_sha256": chunk.source_sha256,
                    "output_path": str(output_path),
                    "output_sha256": sha256_file(output_path),
                }
            )

    status = (
        "PASSED"
        if not (
            missing_outputs
            or extra_outputs
            or duplicates
            or sha_mismatches
            or order_errors
            or empty_outputs
            or metadata_mismatches
        )
        else "FAILED"
    )
    source_manifest_paths = sorted({chunk.source_chunks_manifest_path for chunk in chunks if chunk.source_chunks_manifest_path})
    items = [
        {
            "chapter_index": row["chapter_index"],
            "chunk_index": row["chunk_index"],
            "source_chunk_id": row["source_chunk_id"],
            "source_path": row["source_path"],
            "source_sha256": row["source_sha256"],
            "refined_output_path": row["output_path"],
            "refined_output_sha256": row["output_sha256"],
            "validation_status": "PASSED",
            "merge_position": index,
        }
        for index, row in enumerate(ordered_outputs, 1)
    ]
    return {
        "run_id": expected_run_id,
        "book_id": expected_book_id,
        "language": expected_lang,
        "lang": expected_lang,
        "stage": expected_stage,
        "source_chunks_manifest_path": source_manifest_paths[0] if source_manifest_paths else "",
        "total_expected_chunks": len(chunks),
        "total_processed_chunks": len(output_files),
        "total_merged_chunks": len(ordered_outputs) if status == "PASSED" else 0,
        "expected_count": len(chunks),
        "received_count": len(output_files) - len(extra_outputs),
        "ordered_outputs": ordered_outputs,
        "items": items,
        "missing_outputs": missing_outputs,
        "extra_outputs": extra_outputs,
        "duplicates": duplicates,
        "sha256_mismatches": sha_mismatches,
        "empty_outputs": empty_outputs,
        "metadata_mismatches": metadata_mismatches,
        "order_errors": order_errors,
        "expected_paths": [str(run_dir / chunk.output_filename) for chunk in sorted(chunks, key=_sort_key)],
        "found_paths": [str(path) for path in output_files],
        "expected_run_id": expected_run_id,
        "found_run_ids": sorted({str(_load_output_metadata(metadata_path_for_output(path)).get("run_id") or "") for path in output_files}),
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def write_refine_manifest(run_dir: Path, validation: dict[str, Any]) -> Path:
    manifest_path = run_dir / REFINE_MANIFEST_NAME
    manifest_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def write_merge_report(run_dir: Path, validation: dict[str, Any]) -> Path:
    report_path = run_dir / MERGE_REPORT_NAME
    report_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report_path


def merge_refine_run_by_manifest(
    *,
    run_dir: Path,
    chunks: list[RefineChunk],
    out_path: Path,
    book_code: str | None = None,
    language: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    validation = validate_refine_run(run_dir, chunks)
    manifest_path = write_refine_manifest(run_dir, validation)
    report_path = write_merge_report(run_dir, validation)
    if validation["status"] != "PASSED":
        raise ValueError(f"Refine run validation failed: {manifest_path}; merge report: {report_path}")

    parts: list[str] = []
    infer_initial_book_one = _should_infer_initial_book_one(chunks)
    for index, chunk in enumerate(sorted(chunks, key=_sort_key)):
        source_text = Path(chunk.source_path).read_text(encoding="utf-8")
        output_path = run_dir / chunk.output_filename
        candidate_text = output_path.read_text(encoding="utf-8")
        canonical = canonical_merge.canonicalize_chunk_text(source_text, candidate_text).rstrip()
        if index == 0 and infer_initial_book_one and not _has_book_heading(canonical):
            canonical = f"BOOK I\n\n{canonical}"
        output_path.write_text(canonical + "\n", encoding="utf-8")
        write_refine_output_metadata(output_path, chunk)
        if canonical:
            parts.append(canonical)

    merged = "\n\n".join(parts).rstrip() + "\n"
    if not merged.strip():
        raise FileNotFoundError(f"No ordered refine outputs found in {run_dir}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(merged, encoding="utf-8")
    validation = validate_refine_run(run_dir, chunks)
    validation["merge_path"] = str(out_path)
    validation["merge_report_path"] = str(run_dir / MERGE_REPORT_NAME)
    validation["total_merged_chunks"] = len(parts)
    validation["status"] = "PASSED"
    write_refine_manifest(run_dir, validation)
    write_merge_report(run_dir, validation)
    return out_path, validation


def load_chunks_for_existing_run(run_dir: Path) -> list[RefineChunk]:
    manifest_path = run_dir / REFINE_MANIFEST_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"refine_manifest.json not found for run: {run_dir}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = payload.get("ordered_outputs") or []
    run_id = str(payload.get("run_id") or run_dir.name)
    book_id = str(payload.get("book_id") or "")
    lang = str(payload.get("lang") or "")
    chunks: list[RefineChunk] = []
    for row in rows:
        source_path = str(row.get("source_path") or "")
        output_path = Path(str(row.get("output_path") or ""))
        chunks.append(
            RefineChunk(
                book_id=book_id,
                lang=lang,
                stage=str(payload.get("stage") or "refine"),
                run_id=run_id,
                source_chunk_id=str(row.get("source_chunk_id") or Path(source_path).stem),
                chapter_index=int(row.get("chapter_index") or 0),
                chunk_index=int(row.get("chunk_index") or 0),
                source_path=source_path,
                source_sha256=str(row.get("source_sha256") or ""),
                output_filename=output_path.name,
                source_chunks_manifest_path=str(payload.get("source_chunks_manifest_path") or ""),
            )
        )
    return chunks


BOOK_HEADING_RE = re.compile(r"^\s*(?:#{1,6}\s*)?BOOK\s+([IVXLCDM]+|\d+)\b.*$", re.IGNORECASE | re.MULTILINE)
CHAPTER_HEADING_RE = re.compile(r"^\s*(?:#{1,6}\s*)?Chapter\s+(\d+|[IVXLCDM]+)\b.*$", re.IGNORECASE | re.MULTILINE)


def detect_book_chapter_sequence(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    sequence: list[str] = []
    for line in text.splitlines():
        book_match = BOOK_HEADING_RE.match(line)
        if book_match:
            sequence.append(line.strip())
            continue
        chapter_match = CHAPTER_HEADING_RE.match(line)
        if chapter_match:
            sequence.append(line.strip())
    return sequence
