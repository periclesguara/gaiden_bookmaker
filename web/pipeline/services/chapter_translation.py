from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from editorial.models import EditionPipeline, PipelineArtifact, PipelineStage
from gaiden.application.translation.chapter_splitter import (
    SPLITTER_VERSION,
    ChapterSplitError,
    SplitResult,
    SplitUnit,
    split_heading_clean,
)
from gaiden.infrastructure import storage
from pipeline.models import ManualTranslationJob, TranslationJobEvent, TranslationUnit
from pipeline.services import manual_translation, paths
from pipeline.services.incremental_export import RclonePublisher


JOB_SCHEMA_V2 = "gaiden_manual_translation_job_v2"
STYLE_SCHEMA = "gaiden_translation_style_contract_v1"
REPORT_SCHEMA = "gaiden_chapter_translation_report_v1"
SAFE_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
MODEL_MESSAGE_RE = re.compile(
    r"(?:here is (?:the|your)|as an ai|i (?:cannot|can't)|translation follows|translated chapter)",
    re.IGNORECASE,
)


class ChapterDriveGateway(Protocol):
    def publish_bytes(self, relative_path: str, data: bytes) -> None: ...
    def read_bytes(self, relative_path: str) -> bytes: ...
    def stat(self, relative_path: str) -> dict[str, object] | None: ...
    def list_files(self, relative_directory: str) -> list[dict[str, object]]: ...


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _relpath(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(storage.repo_root()).as_posix())
    except ValueError:
        return str(resolved)


def _job_root(job: ManualTranslationJob) -> Path:
    return storage.data_dir() / "translation_jobs" / job.edition.work.code / job.target_language


def _local_input_path(job: ManualTranslationJob, unit: TranslationUnit) -> Path:
    return _job_root(job) / "input" / "chapters" / unit.input_filename


def _local_return_path(job: ManualTranslationJob, unit: TranslationUnit) -> Path:
    return _job_root(job) / "return" / "chapters" / unit.expected_return_filename


def _write_atomic(path: Path, data: bytes, *, allow_replace: bool = False) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_bytes()
        if existing == data:
            return False
        if not allow_replace:
            raise FileExistsError(f"Artefato divergente já existe: {path.name}")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if _sha256(Path(temp_name).read_bytes()) != _sha256(data):
            raise OSError(f"Falha ao verificar escrita temporária de {path.name}.")
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return True


def _record_event(
    job: ManualTranslationJob,
    *,
    operation: str,
    previous_status: str,
    new_status: str,
    unit: TranslationUnit | None = None,
    origin: str = "gaiden",
    error: str = "",
    detail: dict[str, object] | None = None,
) -> None:
    TranslationJobEvent.objects.create(
        translation_job=job,
        unit=unit,
        operation=operation,
        previous_status=previous_status,
        new_status=new_status,
        origin=origin,
        correlation_id=str(job.correlation_id),
        error=error,
        detail=detail or {},
    )


def _set_status(
    job: ManualTranslationJob,
    status: str,
    operation: str,
    *,
    error: str = "",
    detail: dict[str, object] | None = None,
) -> None:
    previous = job.status
    job.status = status
    job.last_error = error
    job.save(update_fields=["status", "last_error", "updated_at"])
    _record_event(
        job,
        operation=operation,
        previous_status=previous,
        new_status=status,
        error=error,
        detail=detail,
    )


def _roman_to_int(value: str) -> int | None:
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    token = value.upper()
    if not token or any(char not in values for char in token):
        return None
    total = 0
    previous = 0
    for char in reversed(token):
        current = values[char]
        total += -current if current < previous else current
        previous = max(previous, current)
    return total


def _chapter_token(value: str, sequence: int) -> str:
    if value.isdigit():
        return f"{int(value):02d}"
    roman = _roman_to_int(value)
    if roman is not None:
        return f"{roman:02d}"
    return f"{sequence:02d}"


def _unit_filename(unit: SplitUnit) -> str:
    prefix = unit.unit_id
    if unit.unit_type == "chapter":
        label = f"chapter_{_chapter_token(unit.chapter_number, unit.sequence)}"
    elif unit.unit_type == "oversized_chapter_part":
        label = (
            f"chapter_{_chapter_token(unit.chapter_number, unit.sequence)}"
            f"_part_{int(unit.part_number or 1):02d}"
        )
    else:
        label = unit.unit_type
    return f"{prefix}__{label}.txt"


def _return_filename(input_filename: str, target_language: str) -> str:
    return f"{Path(input_filename).stem}__{target_language}.txt"


def _source_unit_bytes(job: ManualTranslationJob, unit: TranslationUnit) -> bytes:
    source_path = Path(job.source_path)
    data = source_path.read_bytes()
    if _sha256(data) != job.source_sha256:
        raise ChapterSplitError("O heading_clean foi alterado depois da criação do split.")
    text = data.decode("utf-8", errors="strict")
    segment = text[unit.source_start_offset : unit.source_end_offset].encode("utf-8")
    if len(segment) != unit.source_size_bytes or _sha256(segment) != unit.source_text_sha256:
        raise ChapterSplitError(f"A unidade {unit.unit_id} diverge do heading_clean registrado.")
    return segment


def _source_artifact(edition, source_path: Path, source_sha256: str, size_bytes: int) -> PipelineArtifact:
    stat = source_path.stat()
    artifact, _ = PipelineArtifact.objects.update_or_create(
        work_code=edition.work.code,
        language_code=edition.language.code,
        stage="heading_clean",
        relpath=_relpath(source_path),
        defaults={
            "filename": source_path.name,
            "size_bytes": size_bytes,
            "sha256": source_sha256,
            "mtime_iso": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "exists": True,
            "is_candidate": True,
        },
    )
    return artifact


def _split_manifest(result: SplitResult, target_language: str) -> dict[str, object]:
    manifest = result.as_manifest()
    units = []
    for unit in result.units:
        input_filename = _unit_filename(unit)
        row = unit.as_dict()
        row.update(
            {
                "input_filename": input_filename,
                "expected_return_filename": _return_filename(input_filename, target_language),
                "separator_after": "",
            }
        )
        units.append(row)
    manifest["units"] = units
    return manifest


def prepare_chapter_job(
    *,
    edition,
    target_edition,
    target_language: str,
    translation_mode: str,
    source_path: Path,
    force: bool = False,
    qwen_detector=None,
) -> ManualTranslationJob:
    target_language = target_language.strip().lower().replace("-", "_")
    manual_translation.drive_target_folder(target_language)
    if translation_mode not in dict(ManualTranslationJob.MODE_CHOICES):
        raise ValueError("Modo de tradução inválido.")
    if not source_path.is_file():
        raise FileNotFoundError(f"Heading Cleaner não encontrado: {source_path}")
    source = source_path.read_bytes()
    result = split_heading_clean(
        source,
        alert_characters=settings.GAIDEN_CHAPTER_SPLIT_ALERT_CHARACTERS,
        hard_limit_characters=settings.GAIDEN_CHAPTER_SPLIT_HARD_LIMIT_CHARACTERS,
        qwen_detector=qwen_detector,
        qwen_confidence_threshold=settings.GAIDEN_CHAPTER_SPLIT_QWEN_CONFIDENCE,
    )
    existing = ManualTranslationJob.objects.filter(edition=edition, target_language=target_language).first()
    if existing and existing.schema_version != JOB_SCHEMA_V2:
        raise ValueError("Já existe um job v1 para este livro e idioma; ele permanece no fluxo legado.")
    resplittable = {
        ManualTranslationJob.STATUS_SPLIT_PENDING,
        ManualTranslationJob.STATUS_SPLITTING,
        ManualTranslationJob.STATUS_SPLIT_REVIEW_REQUIRED,
        ManualTranslationJob.STATUS_SPLIT_VALIDATED,
        ManualTranslationJob.STATUS_FAILED_RETRYABLE,
    }
    if existing and existing.status not in resplittable:
        raise ValueError("O split não pode ser refeito depois do início da exportação ao Drive.")
    if existing and not force:
        if existing.source_sha256 == result.source_sha256 and existing.split_manifest == _split_manifest(result, target_language):
            return existing
        raise ValueError("Já existe um split divergente; use a ação explícita Refazer split.")

    source_artifact = _source_artifact(edition, source_path, result.source_sha256, len(source))
    manifest = _split_manifest(result, target_language)
    expected_name = f"{edition.work.code}_{target_language}_translated.txt"
    defaults = {
        "target_edition": target_edition,
        "job_id": f"{edition.work.code}__{target_language}",
        "source_artifact": source_artifact,
        "final_artifact": None,
        "source_language": edition.language.code,
        "translation_mode": translation_mode,
        "schema_version": JOB_SCHEMA_V2,
        "splitter_version": SPLITTER_VERSION,
        "split_strategy": result.strategy,
        "chapter_count": sum(
            row.unit_type == "chapter"
            or (row.unit_type == "oversized_chapter_part" and row.part_number == 1)
            for row in result.units
        ),
        "split_manifest": manifest,
        "validation_report": {
            "split_validated": result.validated,
            "review_required": result.review_required,
            "warnings": list(result.warnings),
        },
        "drive_path": manual_translation.drive_job_path(edition.work.code, target_language),
        "source_path": str(source_path.resolve()),
        "source_sha256": result.source_sha256,
        "expected_return_name": expected_name,
        "status": (
            ManualTranslationJob.STATUS_SPLIT_VALIDATED
            if result.validated
            else ManualTranslationJob.STATUS_SPLIT_REVIEW_REQUIRED
        ),
        "return_source": "",
        "return_sha256": "",
        "final_sha256": "",
        "last_error": "",
        "imported_at": None,
        "completed_at": None,
    }
    with transaction.atomic():
        job, created = ManualTranslationJob.objects.update_or_create(
            edition=edition,
            target_language=target_language,
            defaults=defaults,
        )
        previous_status = ManualTranslationJob.STATUS_SPLIT_PENDING if created else existing.status
        job.units.all().delete()
        model_units: list[TranslationUnit] = []
        for source_unit, row in zip(result.units, manifest["units"]):
            model_units.append(
                TranslationUnit(
                    translation_job=job,
                    unit_id=source_unit.unit_id,
                    sequence=source_unit.sequence,
                    unit_type=source_unit.unit_type,
                    chapter_number=source_unit.chapter_number,
                    part_number=source_unit.part_number,
                    heading=source_unit.heading,
                    source_start_offset=source_unit.start_offset,
                    source_end_offset=source_unit.end_offset,
                    source_text_sha256=source_unit.source_sha256,
                    source_size_bytes=source_unit.source_size_bytes,
                    input_filename=row["input_filename"],
                    expected_return_filename=row["expected_return_filename"],
                    status=TranslationUnit.STATUS_SPLIT,
                    validation_report={
                        "split_confidence": source_unit.confidence,
                        "split_evidence": source_unit.evidence,
                        "oversized": source_unit.oversized,
                    },
                )
            )
        TranslationUnit.objects.bulk_create(model_units)
        for model_unit in job.units.order_by("sequence"):
            _record_event(
                job,
                unit=model_unit,
                operation="UNIT_SPLIT",
                previous_status=TranslationUnit.STATUS_PENDING,
                new_status=TranslationUnit.STATUS_SPLIT,
                detail={"source_sha256": model_unit.source_text_sha256},
            )
        _record_event(
            job,
            operation="SPLIT_CREATED" if created else "SPLIT_RECREATED",
            previous_status=previous_status,
            new_status=job.status,
            detail={
                "unit_count": len(model_units),
                "source_sha256": result.source_sha256,
                "strategy": result.strategy,
            },
        )

    for unit in job.units.order_by("sequence"):
        _write_atomic(
            _local_input_path(job, unit),
            _source_unit_bytes(job, unit),
            allow_replace=force,
        )
    _write_atomic(_job_root(job) / "split-manifest.json", _json_bytes(manifest), allow_replace=force)
    return job


def _style_contract(job: ManualTranslationJob) -> dict[str, object]:
    return {
        "schema": STYLE_SCHEMA,
        "job_id": job.job_id,
        "translation_mode": job.translation_mode,
        "source_language": job.source_language,
        "target_language": job.target_language,
        "reasoning_effort": "high",
        "rules": [
            "Use contemporary American English when target_language is en_us.",
            "Preserve plot, scenes, characters, names, facts, dialogue force, and event order.",
            "Preserve chapter headings and paragraph separation.",
            "Do not summarize, expand, censor, explain, or invent content.",
            "Do not add translator notes, commentary, Markdown fences, or introductory messages.",
            "Preserve irony, humor, ambiguity, names, titles, relationships, and locations.",
            "Write only the final text for the requested unit.",
        ],
    }


def _job_contract(job: ManualTranslationJob) -> dict[str, object]:
    units = []
    for unit in job.units.order_by("sequence"):
        units.append(
            {
                "unit_id": unit.unit_id,
                "sequence": unit.sequence,
                "unit_type": unit.unit_type,
                "chapter_number": unit.chapter_number or None,
                "part_number": unit.part_number,
                "heading": unit.heading,
                "input_file": f"input/chapters/{unit.input_filename}",
                "input_sha256": unit.source_text_sha256,
                "expected_return_file": f"return/chapters/{unit.expected_return_filename}",
                "separator_after": "",
            }
        )
    return {
        "schema": JOB_SCHEMA_V2,
        "job_id": job.job_id,
        "book_code": job.edition.work.code,
        "title": job.edition.title or job.edition.work.title,
        "author": job.edition.author or job.edition.work.author.name,
        "source_language": job.source_language,
        "target_language": job.target_language,
        "translation_mode": job.translation_mode,
        "source": {
            "artifact_id": job.source_artifact_id,
            "file": Path(job.source_path).name,
            "sha256": job.source_sha256,
            "size_bytes": Path(job.source_path).stat().st_size,
        },
        "split": {
            "strategy": job.split_strategy,
            "splitter_version": job.splitter_version,
            "unit_count": len(units),
        },
        "return": {
            "directory": "return",
            "chapters_directory": "return/chapters",
            "manifest_template": "return/translation-return.template.json",
            "manifest": "return/translation-return.json",
            "expected_final_file": job.expected_return_name,
        },
        "units": units,
    }


def _return_manifest_template(job: ManualTranslationJob) -> dict[str, object]:
    return {
        "schema": "gaiden_manual_translation_return_v2",
        "job_id": job.job_id,
        "book_code": job.edition.work.code,
        "target_language": job.target_language,
        "source_sha256": job.source_sha256,
        "units": [
            {
                "unit_id": unit.unit_id,
                "expected_return_file": f"return/chapters/{unit.expected_return_filename}",
                "return_sha256": "",
            }
            for unit in job.units.order_by("sequence")
        ],
    }


def _publish_immutable(gateway: ChapterDriveGateway, relative_path: str, data: bytes) -> bool:
    safe_path = PurePosixPath(relative_path)
    if safe_path.is_absolute() or ".." in safe_path.parts:
        raise ValueError("Caminho inseguro para publicação no Drive.")
    existing = gateway.stat(str(safe_path))
    if existing is not None:
        remote_data = gateway.read_bytes(str(safe_path))
        if _sha256(remote_data) != _sha256(data):
            raise FileExistsError(f"O Drive já contém conteúdo divergente em {safe_path}.")
        return False
    gateway.publish_bytes(str(safe_path), data)
    return True


def export_chapter_job(
    job: ManualTranslationJob,
    *,
    gateway: ChapterDriveGateway | None = None,
) -> dict[str, object]:
    if job.schema_version != JOB_SCHEMA_V2:
        raise ValueError("Somente jobs v2 podem usar exportação por capítulos.")
    if job.status == ManualTranslationJob.STATUS_DRIVE_READY:
        return {"status": "NO_OP", "unit_count": job.units.count(), "drive_path": job.drive_path}
    if job.status not in {
        ManualTranslationJob.STATUS_SPLIT_VALIDATED,
        ManualTranslationJob.STATUS_FAILED_RETRYABLE,
    }:
        raise ValueError("O split precisa estar validado antes da exportação ao Drive.")
    active_gateway = gateway or RclonePublisher(job.drive_path)
    _set_status(job, ManualTranslationJob.STATUS_DRIVE_EXPORTING, "DRIVE_EXPORT_STARTED")
    published = 0
    try:
        for unit in job.units.order_by("sequence"):
            data = _source_unit_bytes(job, unit)
            if _publish_immutable(active_gateway, f"input/chapters/{unit.input_filename}", data):
                published += 1
            stat = active_gateway.stat(f"input/chapters/{unit.input_filename}") or {}
            previous = unit.status
            unit.drive_input_file_id = str(stat.get("ID") or stat.get("Id") or "")
            unit.status = TranslationUnit.STATUS_EXPORTED
            unit.save(update_fields=["drive_input_file_id", "status", "updated_at"])
            if previous != unit.status:
                _record_event(
                    job,
                    unit=unit,
                    operation="UNIT_EXPORTED",
                    previous_status=previous,
                    new_status=unit.status,
                    origin="google_drive",
                    detail={"source_sha256": unit.source_text_sha256},
                )
        _publish_immutable(active_gateway, "input/style-contract.json", _json_bytes(_style_contract(job)))
        _publish_immutable(active_gateway, "input/translation-job.json", _json_bytes(_job_contract(job)))
        _publish_immutable(
            active_gateway,
            "return/RETURN_HERE.txt",
            (
                "Grave cada unidade traduzida em return/chapters com o nome definido em "
                "input/translation-job.json. Não sobrescreva retornos divergentes.\n"
            ).encode("utf-8"),
        )
        _publish_immutable(
            active_gateway,
            "return/chapters/RETURN_HERE.txt",
            b"Grave aqui somente os arquivos definidos em translation-job.json.\n",
        )
        _publish_immutable(
            active_gateway,
            "return/translation-return.template.json",
            _json_bytes(_return_manifest_template(job)),
        )
        root_stat = active_gateway.stat("") or {}
        input_stat = active_gateway.stat("input") or {}
        return_stat = active_gateway.stat("return") or {}
        job.drive_root_folder_id = str(root_stat.get("ID") or root_stat.get("Id") or "")
        job.input_folder_id = str(input_stat.get("ID") or input_stat.get("Id") or "")
        job.return_folder_id = str(return_stat.get("ID") or return_stat.get("Id") or "")
        job.save(update_fields=["drive_root_folder_id", "input_folder_id", "return_folder_id", "updated_at"])
        _set_status(
            job,
            ManualTranslationJob.STATUS_DRIVE_READY,
            "DRIVE_EXPORT_COMPLETED",
            detail={"unit_count": job.units.count(), "published": published},
        )
    except FileExistsError as exc:
        _set_status(job, ManualTranslationJob.STATUS_CONFLICT, "DRIVE_EXPORT_CONFLICT", error=str(exc))
        raise
    except (OSError, ValueError, ChapterSplitError) as exc:
        _set_status(job, ManualTranslationJob.STATUS_FAILED_RETRYABLE, "DRIVE_EXPORT_FAILED", error=str(exc))
        raise
    return {"status": "DRIVE_READY", "unit_count": job.units.count(), "published": published, "drive_path": job.drive_path}


def _return_row_name(row: dict[str, object]) -> str:
    value = str(row.get("Name") or row.get("Path") or "")
    if not value or PurePosixPath(value).name != value or not SAFE_FILE_RE.fullmatch(value):
        raise ValueError("O Drive retornou um nome de arquivo inseguro.")
    return value


def discover_chapter_returns(
    job: ManualTranslationJob,
    *,
    gateway: ChapterDriveGateway | None = None,
) -> dict[str, object]:
    if job.schema_version != JOB_SCHEMA_V2:
        raise ValueError("Jobs v1 continuam usando o importador monolítico.")
    if job.status not in {
        ManualTranslationJob.STATUS_DRIVE_READY,
        ManualTranslationJob.STATUS_TRANSLATION_IN_PROGRESS,
        ManualTranslationJob.STATUS_PARTIAL_RETURN,
        ManualTranslationJob.STATUS_RETURNS_READY,
        ManualTranslationJob.STATUS_FAILED_RETRYABLE,
        ManualTranslationJob.STATUS_CONFLICT,
    }:
        raise ValueError("O job não está aguardando retornos por capítulo.")
    active_gateway = gateway or RclonePublisher(job.drive_path)
    expected = {unit.expected_return_filename: unit for unit in job.units.all()}
    rows = [
        row
        for row in active_gateway.list_files("return/chapters")
        if str(row.get("Name") or row.get("Path") or "") != "RETURN_HERE.txt"
    ]
    names = [_return_row_name(row) for row in rows]
    if len(names) != len(set(names)):
        error = "O Drive retornou nomes duplicados em return/chapters."
        _set_status(job, ManualTranslationJob.STATUS_CONFLICT, "RETURN_DISCOVERY_DUPLICATE", error=error)
        raise ValueError(error)
    manifest_units: dict[str, dict[str, object]] = {}
    if rows:
        return_manifest_stat = active_gateway.stat("return/translation-return.json")
        if return_manifest_stat is None:
            error = "translation-return.json é obrigatório quando existem retornos por capítulo."
            _set_status(job, ManualTranslationJob.STATUS_CONFLICT, "RETURN_MANIFEST_MISSING", error=error)
            raise ValueError(error)
        try:
            return_manifest = json.loads(active_gateway.read_bytes("return/translation-return.json"))
        except json.JSONDecodeError as exc:
            error = "translation-return.json não contém JSON válido."
            _set_status(job, ManualTranslationJob.STATUS_CONFLICT, "RETURN_MANIFEST_INVALID", error=error)
            raise ValueError(error) from exc
        expected_manifest_values = {
            "schema": "gaiden_manual_translation_return_v2",
            "job_id": job.job_id,
            "book_code": job.edition.work.code,
            "target_language": job.target_language,
            "source_sha256": job.source_sha256,
        }
        mismatches = [
            key for key, expected_value in expected_manifest_values.items()
            if not isinstance(return_manifest, dict) or return_manifest.get(key) != expected_value
        ]
        if mismatches:
            error = "translation-return.json diverge do job em: " + ", ".join(mismatches)
            _set_status(job, ManualTranslationJob.STATUS_CONFLICT, "RETURN_MANIFEST_CONFLICT", error=error)
            raise ValueError(error)
        manifest_rows = return_manifest.get("units")
        if not isinstance(manifest_rows, list) or any(not isinstance(row, dict) for row in manifest_rows):
            error = "translation-return.json precisa declarar a lista de unidades do job."
            _set_status(job, ManualTranslationJob.STATUS_CONFLICT, "RETURN_MANIFEST_UNITS_INVALID", error=error)
            raise ValueError(error)
        for manifest_row in manifest_rows:
            unit_id = str(manifest_row.get("unit_id") or "")
            if not unit_id or unit_id in manifest_units:
                error = "translation-return.json contém unit_id vazio ou duplicado."
                _set_status(job, ManualTranslationJob.STATUS_CONFLICT, "RETURN_MANIFEST_UNIT_CONFLICT", error=error)
                raise ValueError(error)
            manifest_units[unit_id] = manifest_row
        expected_units = {unit.unit_id: unit for unit in expected.values()}
        if set(manifest_units) != set(expected_units):
            error = "translation-return.json não corresponde às unidades esperadas do job."
            _set_status(job, ManualTranslationJob.STATUS_CONFLICT, "RETURN_MANIFEST_UNIT_CONFLICT", error=error)
            raise ValueError(error)
        for unit_id, unit in expected_units.items():
            declared_path = manifest_units[unit_id].get("expected_return_file")
            if declared_path != f"return/chapters/{unit.expected_return_filename}":
                error = f"translation-return.json diverge no arquivo esperado da unidade {unit_id}."
                _set_status(job, ManualTranslationJob.STATUS_CONFLICT, "RETURN_MANIFEST_UNIT_CONFLICT", error=error)
                raise ValueError(error)
    unknown = sorted(set(names) - set(expected))
    if unknown:
        error = "Arquivos desconhecidos em return/chapters: " + ", ".join(unknown)
        _set_status(job, ManualTranslationJob.STATUS_CONFLICT, "RETURN_DISCOVERY_CONFLICT", error=error)
        raise ValueError(error)
    imported = 0
    noop = 0
    conflicts = 0
    for row, name in zip(rows, names):
        unit = expected[name]
        data = active_gateway.read_bytes(f"return/chapters/{name}")
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            previous = unit.status
            unit.status = TranslationUnit.STATUS_REJECTED
            unit.validation_report = {"errors": ["return_not_utf8"]}
            unit.retry_count += 1
            unit.save(update_fields=["status", "validation_report", "retry_count", "updated_at"])
            _record_event(job, unit=unit, operation="UNIT_RETURN_REJECTED", previous_status=previous, new_status=unit.status, origin="google_drive", detail={"errors": ["return_not_utf8"]})
            conflicts += 1
            continue
        if not text.strip():
            previous = unit.status
            unit.status = TranslationUnit.STATUS_REJECTED
            unit.validation_report = {"errors": ["empty_return"]}
            unit.retry_count += 1
            unit.save(update_fields=["status", "validation_report", "retry_count", "updated_at"])
            _record_event(job, unit=unit, operation="UNIT_RETURN_REJECTED", previous_status=previous, new_status=unit.status, origin="google_drive", detail={"errors": ["empty_return"]})
            conflicts += 1
            continue
        digest = _sha256(data)
        declared_hash = str(manifest_units[unit.unit_id].get("return_sha256") or "").strip().lower()
        if declared_hash and declared_hash != digest:
            previous = unit.status
            unit.status = TranslationUnit.STATUS_CONFLICT
            unit.validation_report = {"errors": ["declared_return_hash_mismatch"]}
            unit.retry_count += 1
            unit.save(update_fields=["status", "validation_report", "retry_count", "updated_at"])
            _record_event(
                job,
                unit=unit,
                operation="UNIT_RETURN_CONFLICT",
                previous_status=previous,
                new_status=unit.status,
                origin="google_drive",
                detail={"errors": ["declared_return_hash_mismatch"]},
            )
            conflicts += 1
            continue
        if unit.return_sha256:
            if unit.return_sha256 == digest:
                noop += 1
                continue
            if unit.status in {
                TranslationUnit.STATUS_FAILED_RETRYABLE,
                TranslationUnit.STATUS_REJECTED,
            }:
                previous = unit.status
                current_path = _local_return_path(job, unit)
                if current_path.is_file():
                    archive_path = (
                        _job_root(job)
                        / "return"
                        / "rejected"
                        / f"{unit.unit_id}__{unit.return_sha256}.txt"
                    )
                    _write_atomic(archive_path, current_path.read_bytes())
                _write_atomic(current_path, data, allow_replace=True)
                unit.drive_return_file_id = str(row.get("ID") or row.get("Id") or "")
                unit.return_sha256 = digest
                unit.return_size_bytes = len(data)
                unit.status = TranslationUnit.STATUS_RETURNED
                unit.returned_at = timezone.now()
                unit.retry_count += 1
                unit.save(
                    update_fields=[
                        "drive_return_file_id",
                        "return_sha256",
                        "return_size_bytes",
                        "status",
                        "returned_at",
                        "retry_count",
                        "updated_at",
                    ]
                )
                _record_event(
                    job,
                    unit=unit,
                    operation="UNIT_RETURN_RETRY_ACCEPTED",
                    previous_status=previous,
                    new_status=unit.status,
                    origin="google_drive",
                    detail={"return_sha256": digest, "size_bytes": len(data)},
                )
                imported += 1
                continue
            previous = unit.status
            unit.status = TranslationUnit.STATUS_CONFLICT
            unit.validation_report = {"errors": ["divergent_return_hash"]}
            unit.retry_count += 1
            unit.save(update_fields=["status", "validation_report", "retry_count", "updated_at"])
            _record_event(job, unit=unit, operation="UNIT_RETURN_CONFLICT", previous_status=previous, new_status=unit.status, origin="google_drive", detail={"errors": ["divergent_return_hash"], "return_sha256": digest})
            conflicts += 1
            continue
        try:
            _write_atomic(_local_return_path(job, unit), data)
        except FileExistsError:
            previous = unit.status
            unit.status = TranslationUnit.STATUS_CONFLICT
            unit.validation_report = {"errors": ["divergent_local_return"]}
            unit.retry_count += 1
            unit.save(update_fields=["status", "validation_report", "retry_count", "updated_at"])
            _record_event(job, unit=unit, operation="UNIT_RETURN_CONFLICT", previous_status=previous, new_status=unit.status, detail={"errors": ["divergent_local_return"]})
            conflicts += 1
            continue
        previous = unit.status
        unit.drive_return_file_id = str(row.get("ID") or row.get("Id") or "")
        unit.return_sha256 = digest
        unit.return_size_bytes = len(data)
        unit.status = TranslationUnit.STATUS_RETURNED
        unit.returned_at = timezone.now()
        unit.save(
            update_fields=[
                "drive_return_file_id",
                "return_sha256",
                "return_size_bytes",
                "status",
                "returned_at",
                "updated_at",
            ]
        )
        _record_event(
            job,
            unit=unit,
            operation="UNIT_RETURN_DISCOVERED",
            previous_status=previous,
            new_status=unit.status,
            origin="google_drive",
            detail={"return_sha256": digest, "size_bytes": len(data)},
        )
        imported += 1
    returned = job.units.filter(status__in=(TranslationUnit.STATUS_RETURNED, TranslationUnit.STATUS_VALIDATED)).count()
    total = job.units.count()
    if conflicts:
        new_status = ManualTranslationJob.STATUS_CONFLICT
    elif returned == total and total:
        new_status = ManualTranslationJob.STATUS_RETURNS_READY
    elif returned:
        new_status = ManualTranslationJob.STATUS_PARTIAL_RETURN
    else:
        new_status = ManualTranslationJob.STATUS_TRANSLATION_IN_PROGRESS
    _set_status(
        job,
        new_status,
        "RETURN_DISCOVERY_COMPLETED",
        detail={"returned": returned, "total": total, "imported": imported, "noop": noop},
    )
    return {"returned": returned, "total": total, "imported": imported, "noop": noop, "conflicts": conflicts}


def _paragraph_count(text: str) -> int:
    return len([part for part in re.split(r"\n[ \t]*\n", text) if part.strip()])


def _proper_names(text: str) -> set[str]:
    ignored = {"chapter", "book", "part", "preface", "introduction", "epilogue", "appendix", "the"}
    found = re.findall(r"\b[A-Z][a-z]{2,}\b", text)
    return {name for name in found if name.casefold() not in ignored and found.count(name) >= 2}


def _validate_unit_return(job: ManualTranslationJob, unit: TranslationUnit) -> dict[str, object]:
    source = _source_unit_bytes(job, unit).decode("utf-8", errors="strict")
    target_path = _local_return_path(job, unit)
    if not target_path.is_file():
        return {"valid": False, "errors": ["return_missing"], "warnings": []}
    data = target_path.read_bytes()
    if _sha256(data) != unit.return_sha256:
        return {"valid": False, "errors": ["return_hash_mismatch"], "warnings": []}
    try:
        target = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return {"valid": False, "errors": ["return_not_utf8"], "warnings": []}
    errors: list[str] = []
    warnings: list[str] = []
    if not target.strip():
        errors.append("empty_return")
    if unit.heading and unit.heading.casefold() not in target.casefold():
        errors.append("heading_missing_or_changed")
    if "```" in target:
        errors.append("markdown_fence")
    if re.search(r"\bTODO\b", target, re.IGNORECASE):
        errors.append("todo_marker")
    if MODEL_MESSAGE_RE.search(target):
        errors.append("model_message_or_refusal")
    size_ratio = len(data) / max(1, unit.source_size_bytes)
    if not settings.GAIDEN_CHAPTER_RETURN_MIN_SIZE_RATIO <= size_ratio <= settings.GAIDEN_CHAPTER_RETURN_MAX_SIZE_RATIO:
        warnings.append("implausible_size_ratio")
    paragraph_ratio = _paragraph_count(target) / max(1, _paragraph_count(source))
    if not settings.GAIDEN_CHAPTER_RETURN_MIN_PARAGRAPH_RATIO <= paragraph_ratio <= settings.GAIDEN_CHAPTER_RETURN_MAX_PARAGRAPH_RATIO:
        warnings.append("paragraph_count_ratio")
    missing_names = sorted(name for name in _proper_names(source) if name not in target)
    if missing_names:
        warnings.append("proper_names_missing")
    return {
        "valid": not errors and not warnings,
        "errors": errors,
        "warnings": warnings,
        "size_ratio": round(size_ratio, 4),
        "paragraph_ratio": round(paragraph_ratio, 4),
        "missing_proper_names": missing_names,
        "source_sha256": unit.source_text_sha256,
        "return_sha256": unit.return_sha256,
    }


def validate_chapter_returns(job: ManualTranslationJob) -> dict[str, object]:
    if job.schema_version != JOB_SCHEMA_V2:
        raise ValueError("Jobs v1 continuam usando a validação monolítica.")
    _set_status(job, ManualTranslationJob.STATUS_VALIDATING_RETURNS, "RETURN_VALIDATION_STARTED")
    reports: list[dict[str, object]] = []
    for unit in job.units.order_by("sequence"):
        previous = unit.status
        report = _validate_unit_return(job, unit)
        unit.validation_report = report
        if report["valid"]:
            unit.status = TranslationUnit.STATUS_VALIDATED
            unit.validated_at = timezone.now()
        elif "return_missing" in report["errors"]:
            unit.status = TranslationUnit.STATUS_EXPORTED
            unit.validated_at = None
        elif report["errors"]:
            unit.status = TranslationUnit.STATUS_REJECTED
            unit.validated_at = None
            unit.retry_count += 1
        else:
            unit.status = TranslationUnit.STATUS_FAILED_RETRYABLE
            unit.validated_at = None
            unit.retry_count += 1
        unit.save(update_fields=["validation_report", "status", "validated_at", "retry_count", "updated_at"])
        _record_event(
            job,
            unit=unit,
            operation="UNIT_VALIDATED" if report["valid"] else "UNIT_VALIDATION_FAILED",
            previous_status=previous,
            new_status=unit.status,
            detail={"errors": report["errors"], "warnings": report["warnings"]},
        )
        reports.append({"unit_id": unit.unit_id, **report})
    total = job.units.count()
    validated = job.units.filter(status=TranslationUnit.STATUS_VALIDATED).count()
    missing = list(job.units.exclude(status=TranslationUnit.STATUS_VALIDATED).values_list("unit_id", flat=True))
    aggregate = {
        "schema": REPORT_SCHEMA,
        "job_id": job.job_id,
        "source_sha256": job.source_sha256,
        "unit_count": total,
        "validated_count": validated,
        "missing_or_invalid_units": missing,
        "units": reports,
    }
    job.validation_report = aggregate
    job.save(update_fields=["validation_report", "updated_at"])
    if total and validated == total:
        _set_status(job, ManualTranslationJob.STATUS_MERGE_READY, "RETURN_VALIDATION_COMPLETED", detail={"validated": validated})
    elif job.units.filter(status__in=(TranslationUnit.STATUS_REJECTED, TranslationUnit.STATUS_CONFLICT)).exists():
        _set_status(job, ManualTranslationJob.STATUS_CONFLICT, "RETURN_VALIDATION_CONFLICT", detail={"validated": validated, "total": total})
    else:
        _set_status(job, ManualTranslationJob.STATUS_FAILED_RETRYABLE, "RETURN_VALIDATION_REVIEW_REQUIRED", detail={"validated": validated, "total": total})
    return aggregate


def _final_artifact(job: ManualTranslationJob, final_path: Path, digest: str, size_bytes: int) -> PipelineArtifact:
    stat = final_path.stat()
    artifact, _ = PipelineArtifact.objects.update_or_create(
        work_code=job.edition.work.code,
        language_code=job.target_language,
        stage="translation_final",
        relpath=_relpath(final_path),
        defaults={
            "filename": final_path.name,
            "size_bytes": size_bytes,
            "sha256": digest,
            "mtime_iso": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "exists": True,
            "is_candidate": True,
        },
    )
    return artifact


def merge_chapter_returns(job: ManualTranslationJob) -> dict[str, object]:
    if job.schema_version != JOB_SCHEMA_V2:
        raise ValueError("Jobs v1 continuam usando o merge monolítico.")
    units = list(job.units.order_by("sequence"))
    if not units or any(unit.status != TranslationUnit.STATUS_VALIDATED for unit in units):
        raise ValueError("O merge permanece bloqueado até 100% das unidades estarem validadas.")
    if job.status == ManualTranslationJob.STATUS_COMPLETED and job.final_artifact_id:
        return {
            "status": "NO_OP",
            "path": job.final_artifact.relpath,
            "sha256": job.final_sha256,
            "unit_count": len(units),
        }
    _set_status(job, ManualTranslationJob.STATUS_MERGING, "MERGE_STARTED")
    chunks: list[bytes] = []
    merge_units: list[dict[str, object]] = []
    for unit in units:
        path = _local_return_path(job, unit)
        data = path.read_bytes()
        if _sha256(data) != unit.return_sha256:
            _set_status(job, ManualTranslationJob.STATUS_CONFLICT, "MERGE_HASH_CONFLICT", error=f"Hash divergente na unidade {unit.unit_id}.")
            raise ValueError(f"Hash divergente na unidade {unit.unit_id}.")
        chunks.append(data)
        merge_units.append(
            {
                "unit_id": unit.unit_id,
                "sequence": unit.sequence,
                "return_sha256": unit.return_sha256,
                "size_bytes": len(data),
                "separator_after": "",
            }
        )
    merged = b"".join(chunks)
    if not merged.strip():
        raise ValueError("O merge resultou em manuscrito vazio.")
    merged.decode("utf-8", errors="strict")
    digest = _sha256(merged)
    final_dir = storage.translated_dir(job.edition.work.code, job.target_language)
    final_path = final_dir / job.expected_return_name
    _write_atomic(final_path, merged)
    manifest = {
        "schema": "gaiden_chapter_merge_manifest_v1",
        "job_id": job.job_id,
        "source_sha256": job.source_sha256,
        "final_file": job.expected_return_name,
        "final_sha256": digest,
        "size_bytes": len(merged),
        "unit_count": len(units),
        "units": merge_units,
    }
    report = {
        **job.validation_report,
        "merge": {
            "valid": True,
            "final_sha256": digest,
            "size_bytes": len(merged),
            "unit_count": len(units),
        },
    }
    _write_atomic(_job_root(job) / "merge-manifest.json", _json_bytes(manifest))
    _write_atomic(_job_root(job) / "validation-report.json", _json_bytes(report))
    target_edition = job.target_edition
    if target_edition is None:
        raise ValueError("O job não possui edição de destino para promoção editorial.")
    merge_translate_path = paths.merge_translate_path(target_edition)
    _write_atomic(merge_translate_path, merged)
    artifact = _final_artifact(job, final_path, digest, len(merged))
    with transaction.atomic():
        previous = job.status
        job.status = ManualTranslationJob.STATUS_MERGED
        job.final_artifact = artifact
        job.final_sha256 = digest
        job.return_sha256 = digest
        job.return_source = str(final_path)
        job.validation_report = report
        job.save(
            update_fields=[
                "status",
                "final_artifact",
                "final_sha256",
                "return_sha256",
                "return_source",
                "validation_report",
                "updated_at",
            ]
        )
        _record_event(job, operation="MERGE_COMPLETED", previous_status=previous, new_status=job.status, detail={"final_sha256": digest})
        previous = job.status
        job.status = ManualTranslationJob.STATUS_VALIDATED
        job.save(update_fields=["status", "updated_at"])
        _record_event(job, operation="FINAL_QA_VALIDATED", previous_status=previous, new_status=job.status, detail={"final_sha256": digest})
        previous = job.status
        job.status = ManualTranslationJob.STATUS_COMPLETED
        job.imported_at = timezone.now()
        job.completed_at = timezone.now()
        job.last_error = ""
        job.save(update_fields=["status", "imported_at", "completed_at", "last_error", "updated_at"])
        _record_event(job, operation="TRANSLATION_COMPLETED", previous_status=previous, new_status=job.status, detail={"final_sha256": digest})
        pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=target_edition)
        pipeline_state.current_stage = PipelineStage.MERGED
        pipeline_state.translation_language = job.target_language
        pipeline_state.translated_at = timezone.now()
        pipeline_state.merged_at = timezone.now()
        pipeline_state.last_log = f"{timezone.now().isoformat()} :: CHAPTER_TRANSLATION_COMPLETED :: sha256={digest}"
        pipeline_state.save(
            update_fields=["current_stage", "translation_language", "translated_at", "merged_at", "last_log"]
        )
    return {"status": "COMPLETED", "path": str(final_path), "sha256": digest, "unit_count": len(units), "manifest": manifest}


def job_progress(job: ManualTranslationJob | None) -> dict[str, object]:
    if job is None or job.schema_version != JOB_SCHEMA_V2:
        return {
            "total": 0,
            "exported": 0,
            "returned": 0,
            "validated": 0,
            "missing": [],
            "conflicts": [],
            "percent": 0,
            "can_merge": False,
        }
    units = list(job.units.order_by("sequence"))
    returned_states = {
        TranslationUnit.STATUS_RETURNED,
        TranslationUnit.STATUS_VALIDATED,
        TranslationUnit.STATUS_FAILED_RETRYABLE,
        TranslationUnit.STATUS_CONFLICT,
        TranslationUnit.STATUS_REJECTED,
    }
    exported_states = returned_states | {TranslationUnit.STATUS_EXPORTED}
    missing = [unit.unit_id for unit in units if unit.status not in returned_states]
    conflicts = [
        unit.unit_id
        for unit in units
        if unit.status in {TranslationUnit.STATUS_FAILED_RETRYABLE, TranslationUnit.STATUS_CONFLICT, TranslationUnit.STATUS_REJECTED}
    ]
    validated = sum(unit.status == TranslationUnit.STATUS_VALIDATED for unit in units)
    total = len(units)
    return {
        "total": total,
        "exported": sum(unit.status in exported_states for unit in units),
        "returned": sum(unit.status in returned_states for unit in units),
        "validated": validated,
        "missing": missing,
        "conflicts": conflicts,
        "percent": round((validated / total) * 100, 1) if total else 0,
        "can_merge": bool(total and validated == total and not conflicts),
    }
