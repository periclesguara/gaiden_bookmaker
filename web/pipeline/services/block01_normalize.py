from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from editorial.models import EditionPipeline, EditionText, PipelineArtifact, PipelineStage
from gaiden.application.normalization import normalize_extracted_text
from gaiden.application.pipeline import ingest as pipeline_ingest
from gaiden.infrastructure import storage


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if _sha256(Path(temporary).read_bytes()) != _sha256(data):
            raise OSError(f"Falha ao verificar a escrita de {path.name}.")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(storage.repo_root()).as_posix())
    except ValueError:
        return str(path.resolve())


def _artifact(edition, *, stage: str, path: Path, digest: str | None = None) -> PipelineArtifact:
    data = path.read_bytes()
    stat = path.stat()
    artifact, _ = PipelineArtifact.objects.update_or_create(
        work_code=edition.work.code,
        language_code=edition.language.code,
        stage=stage,
        relpath=_relpath(path),
        defaults={
            "filename": path.name,
            "size_bytes": len(data),
            "sha256": digest or _sha256(data),
            "mtime_iso": datetime.fromtimestamp(
                stat.st_mtime, tz=datetime_timezone.utc
            ).isoformat(timespec="seconds"),
            "exists": True,
            "is_candidate": True,
        },
    )
    return artifact


def _extract_text(raw_path: Path, book_code: str, language: str) -> tuple[str, str]:
    suffix = raw_path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        return raw_path.read_text(encoding="utf-8-sig", errors="replace"), "raw_text"
    if suffix in pipeline_ingest.source_extract_supported_extensions():
        result = pipeline_ingest.run_source_extract(book_code, language, raw_path)
        canonical = storage.resolve_repo_path(str(result["canonical_txt"]))
        if canonical.is_file():
            return canonical.read_text(encoding="utf-8", errors="replace"), "source_extract"
    text = pipeline_ingest.extract_text_from_file(raw_path, suffix.lstrip("."))
    if not text:
        raise ValueError(f"Não foi possível extrair texto do RAW {raw_path.name}.")
    return text, "pipeline_ingest"


def _deterministic_bibliography(text: str) -> dict[str, object]:
    fields: dict[str, object] = {}
    patterns = {
        "original_title": r"(?im)^title\s*:\s*(.+?)\s*$",
        "source_author": r"(?im)^author\s*:\s*(.+?)\s*$",
        "source_language": r"(?im)^language\s*:\s*(.+?)\s*$",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            fields[key] = {
                "value": match.group(1).strip(),
                "source": "deterministic",
                "status": "suggested",
                "evidence": f"RAW header: {match.group(0).strip()[:120]}",
            }
    gutenberg = re.search(
        r"(?:gutenberg\.org/(?:ebooks|cache/epub)/|ebook(?:\s+number)?\s*#?)(\d{2,8})",
        text,
        re.IGNORECASE,
    )
    if gutenberg:
        identifier = gutenberg.group(1)
        for key, value in {
            "source_platform": "Project Gutenberg",
            "source_identifier": identifier,
            "source_url": f"https://www.gutenberg.org/ebooks/{identifier}",
        }.items():
            fields[key] = {
                "value": value,
                "source": "deterministic",
                "status": "suggested",
                "evidence": f"RAW source identifier: {identifier}",
            }
    return fields


def _stage_provenance(
    edition,
    *,
    raw_path: Path,
    raw_data: bytes,
    original_name: str,
    source_origin: str,
    ingested_at,
    extracted_text: str,
    correlation_id: str,
) -> dict[str, object]:
    current = dict(edition.work.source_provenance or {})
    existing_fields = current.get("fields") if isinstance(current.get("fields"), dict) else {}
    proposed = _deterministic_bibliography(extracted_text)
    merged_fields = dict(existing_fields)
    for key, value in proposed.items():
        existing = merged_fields.get(key)
        if isinstance(existing, dict) and (
            existing.get("source") == "manual" or existing.get("status") in {"edited", "confirmed"}
        ):
            continue
        if key not in merged_fields or not isinstance(existing, dict) or not existing.get("value"):
            merged_fields[key] = value
    prior_state = str(current.get("workflow_status") or "")
    current.update(
        {
            "schema_version": "gaiden_source_provenance_staged_v1",
            "workflow_status": prior_state if prior_state in {"CONFIRMED", "APPROVED"} else "PROVENANCE_STAGED",
            "technical": {
                "source_filename": Path(original_name or raw_path.name).name,
                "source_sha256": _sha256(raw_data),
                "source_size_bytes": len(raw_data),
                "source_format": raw_path.suffix.lower().lstrip("."),
                "source_mime_type": mimetypes.guess_type(original_name or raw_path.name)[0]
                or "application/octet-stream",
                "source_uri": raw_path.resolve().as_uri(),
                "source_origin": source_origin or "local_intake",
                "artifact_identifier": f"{edition.work.code}:{_sha256(raw_data)}",
                "ingested_at": ingested_at.isoformat() if ingested_at else timezone.now().isoformat(),
                "language": edition.language.code,
                "book_code": edition.work.code,
                "edition_id": edition.id,
            },
            "fields": merged_fields,
            "normalize_correlation_id": correlation_id,
            "staged_at": timezone.now().isoformat(),
        }
    )
    edition.work.source_provenance = current
    edition.work.save(update_fields=["source_provenance"])
    return current


def run_normalize(*, edition, source_template, classifier) -> dict[str, object]:
    source_value = (source_template.source_saved_path or edition.raw_source_path or "").strip()
    if not source_value:
        raise FileNotFoundError("O Intake não possui caminho para o RAW original.")
    raw_path = storage.resolve_repo_path(source_value)
    if not raw_path.is_file():
        raise FileNotFoundError(f"RAW original não encontrado: {raw_path}")
    before = raw_path.read_bytes()
    raw_sha = _sha256(before)
    recorded_sha = (source_template.source_file_sha256 or "").strip().lower()
    if recorded_sha and recorded_sha != raw_sha:
        raise ValueError("O RAW diverge do SHA-256 registrado pelo Intake; Normalize interrompido.")
    extracted_text, extraction_source = _extract_text(
        raw_path, edition.work.code, edition.language.code
    )
    result = normalize_extracted_text(
        extracted_text,
        raw_sha256=raw_sha,
        classifier=classifier,
    )
    if raw_path.read_bytes() != before:
        raise RuntimeError("O RAW foi alterado durante o Normalize.")

    correlation_id = uuid.uuid4().hex
    created_at = timezone.now().isoformat()
    normalized_path = storage.normalized_body_path(edition.work.code, edition.language.code)
    manifest_path = storage.normalize_manifest_path(edition.work.code, edition.language.code)
    structure_path = storage.structure_map_path(edition.work.code, edition.language.code)
    manifest = {
        **result.manifest,
        "correlation_id": correlation_id,
        "created_at": created_at,
        "status": "NORMALIZE_REVIEW_REQUIRED" if result.manifest["review_required"] else "NORMALIZED",
        "origin": "gaiden",
        "raw": {
            "filename": Path(source_template.source_original_name or raw_path.name).name,
            "uri": raw_path.resolve().as_uri(),
            "sha256": raw_sha,
            "size_bytes": len(before),
            "mime_type": mimetypes.guess_type(raw_path.name)[0] or "application/octet-stream",
            "artifact_path": _relpath(raw_path),
        },
        "normalized_body_path": _relpath(normalized_path),
        "structure_map_path": _relpath(structure_path),
        "extraction_source": extraction_source,
    }
    structure_map = {
        **result.structure_map,
        "correlation_id": correlation_id,
        "created_at": created_at,
        "status": "NORMALIZE_REVIEW_REQUIRED" if result.structure_map["review_required"] else "NORMALIZED",
        "origin": "gaiden",
    }
    _atomic_write(normalized_path, result.normalized_body.encode("utf-8"))
    _atomic_write(
        manifest_path,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    _atomic_write(
        structure_path,
        (json.dumps(structure_map, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )

    with transaction.atomic():
        raw_artifact = _artifact(edition, stage="raw", path=raw_path, digest=raw_sha)
        normalized_artifact = _artifact(
            edition,
            stage="normalize",
            path=normalized_path,
            digest=str(result.manifest["normalized_sha256"]),
        )
        structure_artifact = _artifact(edition, stage="structure_map", path=structure_path)
        _artifact(edition, stage="normalize", path=manifest_path)
        provenance = _stage_provenance(
            edition,
            raw_path=raw_path,
            raw_data=before,
            original_name=source_template.source_original_name,
            source_origin=source_template.source_uploaded_by,
            ingested_at=source_template.source_uploaded_at,
            extracted_text=extracted_text,
            correlation_id=correlation_id,
        )
        texts, _ = EditionText.objects.get_or_create(edition=edition)
        texts.normalized_text = result.normalized_body
        texts.normalized_path = str(normalized_path)
        texts.save(update_fields=["normalized_text", "normalized_path", "updated_at"])
        state, _ = EditionPipeline.objects.get_or_create(edition=edition)
        if state.raw_at is None:
            state.raw_at = timezone.now()
        state.current_stage = PipelineStage.NORMALIZED
        state.normalized_at = timezone.now()
        state.last_log = (
            f"{created_at} :: {manifest['status']} :: correlation_id={correlation_id} :: "
            f"normalized_sha256={result.manifest['normalized_sha256']}"
        )
        state.save(update_fields=["raw_at", "current_stage", "normalized_at", "last_log"])

    return {
        "status": manifest["status"],
        "correlation_id": correlation_id,
        "raw_artifact_id": raw_artifact.id,
        "normalized_artifact_id": normalized_artifact.id,
        "structure_artifact_id": structure_artifact.id,
        "normalized_path": str(normalized_path),
        "manifest_path": str(manifest_path),
        "structure_map_path": str(structure_path),
        "removed_block_count": manifest["removed_block_count"],
        "structure_count": len(structure_map["structures"]),
        "review_required": manifest["review_required"],
        "provenance_status": provenance["workflow_status"],
    }
