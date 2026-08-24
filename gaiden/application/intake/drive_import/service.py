from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.text import slugify

from editorial.models import Contributor, ContributorRole, Language, Work
from pipeline.models import IntakeAuditEvent, IntakeBatch, IntakeCounter, IntakeItem

from .contracts import DriveStoragePort
from .metadata import filename_metadata, normalize_language, resolve_book_code, text_header_metadata, valid_book_code


CONTROL_NAMES = {"book.manifest.json", "readme.txt"}
CONTROL_SUFFIXES = (".manifest.json", ".csv")
logger = logging.getLogger(__name__)


class IntakeValidationError(ValueError):
    pass


class StaleDrivePreview(IntakeValidationError):
    pass


def _snapshot_payload(source_path: str, recursive: bool, rows: list[dict]) -> dict:
    return {
        "source_path": source_path,
        "recursive": recursive,
        "files": [
            {
                "id": row.get("remote_file_id") or "",
                "path": row["relative_path"],
                "size": row["size"],
                "modified_at": row.get("modified_at") or "",
                "hashes": row.get("hashes") or {},
            }
            for row in rows
        ],
    }


def snapshot_sha256(source_path: str, recursive: bool, rows: list[dict]) -> str:
    data = json.dumps(
        _snapshot_payload(source_path, recursive, rows),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _remote_version(row: dict) -> str:
    payload = {
        "id": row.get("remote_file_id") or "",
        "size": row["size"],
        "modified_at": row.get("modified_at") or "",
        "hashes": row.get("hashes") or {},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _is_control_file(name: str) -> bool:
    lowered = name.casefold()
    return lowered in CONTROL_NAMES or any(lowered.endswith(suffix) for suffix in CONTROL_SUFFIXES)


def _numeric_suffix(value: str, prefix: str) -> int | None:
    match = re.fullmatch(rf"{re.escape(prefix)}([0-9]+)", value or "")
    return int(match.group(1)) if match else None


def _preview_next_batch_code() -> str:
    values = IntakeBatch.objects.values_list("batch_code", flat=True)
    maximum = max((_numeric_suffix(value, "batch_") or 0 for value in values), default=0)
    return f"batch_{maximum + 1:04d}"


def _preview_book_codes(count: int) -> list[str]:
    values = list(Work.objects.values_list("code", flat=True))
    values.extend(IntakeItem.objects.exclude(book_code="").values_list("book_code", flat=True))
    maximum = max((_numeric_suffix(value, "book_") or 0 for value in values), default=0)
    return [f"book_{number:04d}" for number in range(maximum + 1, maximum + count + 1)]


def _allocate_code(*, key: str, prefix: str, width: int, floor: int) -> str:
    try:
        counter = IntakeCounter.objects.select_for_update().get(key=key)
    except IntakeCounter.DoesNotExist:
        try:
            counter = IntakeCounter.objects.create(key=key, next_value=floor)
        except IntegrityError:
            counter = IntakeCounter.objects.select_for_update().get(key=key)
    value = max(counter.next_value, floor)
    counter.next_value = value + 1
    counter.save(update_fields=["next_value", "updated_at"])
    return f"{prefix}{value:0{width}d}"


def _next_batch_floor() -> int:
    values = IntakeBatch.objects.select_for_update().values_list("batch_code", flat=True)
    return max((_numeric_suffix(value, "batch_") or 0 for value in values), default=0) + 1


def _next_book_floor() -> int:
    work_values = Work.objects.select_for_update().values_list("code", flat=True)
    item_values = IntakeItem.objects.select_for_update().exclude(book_code="").values_list("book_code", flat=True)
    values = list(work_values) + list(item_values)
    return max((_numeric_suffix(value, "book_") or 0 for value in values), default=0) + 1


def _operation_for(*, code: str, title: str, author: str, row: dict, existing_item: IntakeItem | None) -> tuple[str, str]:
    if existing_item:
        same_remote = (
            existing_item.remote_version == _remote_version(row)
            and existing_item.size_bytes == row["size"]
            and existing_item.book_code == code
        )
        return ("NO_OP", "Mesmo arquivo remoto e mesma identidade.") if same_remote else ("UPDATE", "Mesmo item remoto com conteúdo ou metadados alterados.")
    claimed = IntakeItem.objects.filter(book_code=code).select_related("batch").first() if code else None
    if claimed:
        return "CONFLICT", f"{code} já pertence ao lote {claimed.batch.batch_code}."
    work = Work.objects.filter(code=code).select_related("author").first() if code else None
    if work:
        if work.title.strip().casefold() != title.strip().casefold():
            return "CONFLICT", f"{code} já pertence a uma obra com outro título."
        if author and work.author.name.strip().casefold() != author.strip().casefold():
            return "CONFLICT", f"{code} já pertence a uma obra com outro autor."
        return "UPDATE", "Código existente compatível; o item será vinculado."
    return "CREATE", "Nova obra e novo item de Intake."


def preview_drive_folder(
    storage: DriveStoragePort,
    *,
    folder: str,
    recursive: bool,
    batch_name: str,
    default_author: str,
    source_language: str,
    target_language: str = "",
    seal: str = "",
) -> dict:
    source_path, discovered = storage.discover(folder, recursive=recursive)
    allowed = set(settings.GAIDEN_INTAKE_ALLOWED_EXTENSIONS)
    max_size = settings.GAIDEN_INTAKE_MAX_FILE_SIZE
    editorial_rows = []
    warnings = []
    for row in discovered:
        extension = PurePosixPath(row["name"]).suffix.casefold()
        if _is_control_file(row["name"]):
            warnings.append(f"Arquivo de controle ignorado: {row['relative_path']}")
            continue
        if row.get("is_link"):
            warnings.append(f"Link/alias não permitido ignorado: {row['relative_path']}")
            continue
        if extension not in allowed:
            warnings.append(f"Extensão não permitida ignorada: {row['relative_path']}")
            continue
        metadata = filename_metadata(row["name"])
        editorial_rows.append({**row, "extension": extension, "detected": metadata})

    proposals = iter(_preview_book_codes(sum(1 for row in editorial_rows if not row["detected"]["book_code"])))
    existing_batch = IntakeBatch.objects.filter(
        source="GOOGLE_DRIVE",
        remote=storage.remote,
        drive_source_path=source_path,
    ).first()
    batch_code = existing_batch.batch_code if existing_batch else _preview_next_batch_code()
    seen_codes: dict[str, str] = {}
    items = []
    for row in editorial_rows:
        existing_item = (
            existing_batch.items.filter(relative_path=row["relative_path"]).first()
            if existing_batch
            else None
        )
        proposed = ""
        if not row["detected"]["book_code"]:
            proposed = existing_item.book_code if existing_item else next(proposals)
        code, conflict = resolve_book_code(
            filename_code=row["detected"]["book_code"],
            proposed_code=proposed,
        )
        title = row["detected"]["title"]
        # A value supplied by the operator is an explicit preview edit and has
        # precedence over the ambiguous middle segment of a filename.
        author = default_author.strip() or row["detected"]["author"]
        operation, reason = _operation_for(
            code=code,
            title=title,
            author=author,
            row=row,
            existing_item=existing_item,
        ) if not conflict else ("CONFLICT", conflict)
        if row["size"] > max_size:
            operation, reason = "CONFLICT", f"Arquivo excede o limite de {max_size} bytes."
        if row["size"] <= 0:
            operation, reason = "CONFLICT", "Arquivo vazio."
        if code in seen_codes:
            operation = "CONFLICT"
            reason = f"book_code repetido na pasta: {seen_codes[code]} e {row['relative_path']}."
            for previous in items:
                if previous["book_code"] == code:
                    previous["operation"] = "CONFLICT"
                    previous["reason"] = reason
        elif code:
            seen_codes[code] = row["relative_path"]
        items.append(
            {
                "remote_file_id": row.get("remote_file_id") or "",
                "remote_path": row["remote_path"],
                "relative_path": row["relative_path"],
                "name": row["name"],
                "extension": row["extension"],
                "mime_type": row.get("mime_type") or "",
                "size": row["size"],
                "modified_at": row.get("modified_at") or "",
                "hashes": row.get("hashes") or {},
                "remote_version": _remote_version(row),
                "title": title,
                "author": author,
                "source_language": source_language,
                "target_language": target_language,
                "book_code": code,
                "code_source": "filename" if row["detected"]["book_code"] else "reservation",
                "operation": operation,
                "reason": reason,
            }
        )
    counts = {name: sum(item["operation"] == name for item in items) for name in ("CREATE", "UPDATE", "NO_OP", "CONFLICT")}
    return {
        "kind": "drive_folder",
        "batch_code": batch_code,
        "batch_name": batch_name.strip() or PurePosixPath(source_path).name,
        "source_path": source_path,
        "remote": storage.remote,
        "recursive": recursive,
        "defaults": {
            "author": default_author.strip(),
            "source_language": source_language.strip().lower(),
            "target_language": target_language.strip().lower(),
            "seal": seal.strip(),
        },
        "snapshot_sha256": snapshot_sha256(source_path, recursive, discovered),
        "items": items,
        "counts": counts,
        "warnings": warnings,
        "can_confirm": any(item["operation"] != "CONFLICT" for item in items),
        "generated_at": timezone.now().isoformat(),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_basic_content(path: Path, extension: str) -> None:
    if path.stat().st_size <= 0:
        raise IntakeValidationError("Arquivo vazio.")
    if extension in {".txt", ".md", ".html", ".htm", ".rtf"}:
        path.read_bytes()[:65536].decode("utf-8", errors="strict")
    elif extension in {".epub", ".docx"} and not zipfile.is_zipfile(path):
        raise IntakeValidationError(f"Assinatura inválida para {extension}.")


def _audit(*, batch: IntakeBatch, item: IntakeItem | None, correlation_id: str, operation: str, previous: str, new: str, attempt: int, detail: dict) -> None:
    IntakeAuditEvent.objects.create(
        batch=batch,
        item=item,
        correlation_id=correlation_id,
        operation=operation,
        previous_status=previous,
        new_status=new,
        attempt=attempt,
        detail=detail,
    )
    logger.info(
        "automated_intake_transition",
        extra={
            "batch_code": batch.batch_code,
            "book_code": item.book_code if item else "",
            "relative_path": item.relative_path if item else "",
            "operation": operation,
            "previous_status": previous,
            "new_status": new,
            "attempt": attempt,
            "correlation_id": correlation_id,
        },
    )


def _ensure_catalog(item_data: dict) -> Work:
    language = Language.objects.filter(code=item_data["source_language"], is_active=True).first()
    if language is None:
        raise IntakeValidationError(f"Idioma ativo não cadastrado: {item_data['source_language']}.")
    author_name = item_data["author"].strip() or "Autor não informado"
    contributor, _ = Contributor.objects.get_or_create(name=author_name, role=ContributorRole.AUTHOR)
    work = Work.objects.select_for_update().filter(code=item_data["book_code"]).select_related("author").first()
    if work:
        if work.title.strip().casefold() != item_data["title"].strip().casefold():
            raise IntakeValidationError(f"{work.code} possui título incompatível.")
        if work.author.name.strip().casefold() != author_name.casefold():
            raise IntakeValidationError(f"{work.code} possui autor incompatível.")
        return work
    return Work.objects.create(
        code=item_data["book_code"],
        title=item_data["title"],
        source_provenance={},
        original_language=language,
        author=contributor,
    )


def _canonical_path(storage: DriveStoragePort, batch: IntakeBatch, item_data: dict) -> str:
    return "/".join(
        (
            storage.imported,
            f"{batch.batch_code}__{batch.slug}",
            item_data["book_code"],
            "source",
            PurePosixPath(item_data["name"]).name,
        )
    )


def confirm_drive_folder(storage: DriveStoragePort, preview: dict, *, selected_paths: list[str] | None = None) -> dict:
    if preview.get("kind") != "drive_folder":
        raise IntakeValidationError("Token de prévia incompatível com importação de pasta.")
    source_path, current_rows = storage.discover(preview["source_path"], recursive=bool(preview["recursive"]))
    current_snapshot = snapshot_sha256(source_path, bool(preview["recursive"]), current_rows)
    if current_snapshot != preview["snapshot_sha256"]:
        raise StaleDrivePreview("A pasta mudou depois da prévia; gere uma nova prévia.")
    if selected_paths is None:
        selected = {item["relative_path"] for item in preview["items"]}
    else:
        selected = set(selected_paths)
    known = {item["relative_path"] for item in preview["items"]}
    if not selected or not selected.issubset(known):
        raise IntakeValidationError("Seleção de arquivos inválida.")
    chosen = [item for item in preview["items"] if item["relative_path"] in selected]
    conflicts = [item for item in chosen if item["operation"] == "CONFLICT"]
    if conflicts:
        raise IntakeValidationError("A seleção contém conflitos bloqueantes.")

    correlation_id = uuid.uuid4().hex
    started = time.monotonic()
    staged: list[tuple[dict, Path, str]] = []
    with storage.staging_directory() as staging_name:
        staging_root = Path(staging_name).resolve()

        def download_and_validate(index_and_item):
            index, item_data = index_and_item
            target = staging_root / f"{index:04d}{item_data['extension']}"
            storage.download_to(item_data["remote_path"], target)
            if target.stat().st_size != item_data["size"]:
                raise IntakeValidationError(f"Tamanho divergente após download: {item_data['relative_path']}")
            _validate_basic_content(target, item_data["extension"])
            headers = text_header_metadata(target.read_bytes()) if item_data["extension"] in {".txt", ".md", ".html", ".htm", ".rtf"} else {"book_code": "", "title": "", "author": "", "language": ""}
            code, conflict = resolve_book_code(
                header_code=headers["book_code"],
                filename_code=item_data["book_code"] if item_data["code_source"] == "filename" else "",
                proposed_code=item_data["book_code"] if item_data["code_source"] == "reservation" else "",
            )
            if conflict or code != item_data["book_code"]:
                raise IntakeValidationError(f"Conflito de identidade em {item_data['relative_path']}: {conflict}")
            confirmed_data = dict(item_data)
            confirmed_data["title"] = headers["title"] or item_data["title"]
            confirmed_data["author"] = headers["author"] or item_data["author"]
            confirmed_data["source_language"] = normalize_language(headers["language"], item_data["source_language"])
            return confirmed_data, target, _sha256_file(target)

        worker_count = min(6, len(chosen))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            staged = list(executor.map(download_and_validate, enumerate(chosen, start=1)))

        promoted_item_ids: list[int] = []
        results = {"created": [], "updated": [], "noop": [], "conflicts": [], "failed": []}
        with transaction.atomic():
            batch = IntakeBatch.objects.select_for_update().filter(
                source="GOOGLE_DRIVE",
                remote=storage.remote,
                drive_source_path=source_path,
            ).first()
            if batch is None:
                batch_code = _allocate_code(key="batch", prefix="batch_", width=4, floor=_next_batch_floor())
                if batch_code != preview["batch_code"]:
                    raise StaleDrivePreview("A sequência de lotes mudou depois da prévia; gere uma nova prévia.")
                batch = IntakeBatch.objects.create(
                    batch_code=batch_code,
                    name=preview["batch_name"],
                    slug=slugify(preview["batch_name"]) or batch_code,
                    source="GOOGLE_DRIVE",
                    remote=storage.remote,
                    drive_source_path=source_path,
                    recursive=bool(preview["recursive"]),
                    defaults=preview["defaults"],
                    status="STAGED",
                )
            else:
                batch.status = "STAGED"
                batch.last_error = ""
                batch.save(update_fields=["status", "last_error", "updated_at"])

            for item_data, local_path, sha256 in staged:
                existing = IntakeItem.objects.select_for_update().filter(
                    batch=batch,
                    relative_path=item_data["relative_path"],
                ).first()
                if item_data["code_source"] == "reservation" and existing is None:
                    allocated = _allocate_code(key="book", prefix="book_", width=4, floor=_next_book_floor())
                    if allocated != item_data["book_code"]:
                        raise StaleDrivePreview("A sequência de book_codes mudou depois da prévia; gere uma nova prévia.")
                _ensure_catalog(item_data)
                if existing and existing.sha256 == sha256 and existing.book_code == item_data["book_code"]:
                    existing.preview_operation = "NO_OP"
                    existing.remote_version = item_data["remote_version"]
                    existing.attempt_count += 1
                    existing.last_error = ""
                    existing.save(update_fields=["preview_operation", "remote_version", "attempt_count", "last_error", "updated_at"])
                    results["noop"].append(item_data["book_code"])
                    _audit(batch=batch, item=existing, correlation_id=correlation_id, operation="NO_OP", previous=existing.status, new=existing.status, attempt=existing.attempt_count, detail={"relative_path": existing.relative_path, "sha256": sha256})
                    continue
                if existing and existing.sha256 and existing.sha256 != sha256:
                    existing.status = "CONFLICT"
                    existing.last_error = "O mesmo item remoto contém bytes diferentes dos já confirmados."
                    existing.attempt_count += 1
                    existing.save(update_fields=["status", "last_error", "attempt_count", "updated_at"])
                    results["conflicts"].append(item_data["book_code"])
                    _audit(batch=batch, item=existing, correlation_id=correlation_id, operation="CONFLICT", previous="REGISTERED", new="CONFLICT", attempt=existing.attempt_count, detail={"relative_path": existing.relative_path})
                    continue
                previous = existing.status if existing else ""
                values = {
                    "remote_file_id": item_data["remote_file_id"],
                    "remote_path": item_data["remote_path"],
                    "original_name": item_data["name"],
                    "size_bytes": item_data["size"],
                    "mime_type": item_data["mime_type"],
                    "extension": item_data["extension"],
                    "remote_version": item_data["remote_version"],
                    "sha256": sha256,
                    "title": item_data["title"],
                    "author_name": item_data["author"],
                    "source_language": item_data["source_language"],
                    "target_language": item_data["target_language"],
                    "book_code": item_data["book_code"],
                    "preview_operation": "CREATE" if existing is None else "UPDATE",
                    "status": "STAGED",
                    "last_error": "",
                    "attempt_count": (existing.attempt_count if existing else 0) + 1,
                    "metadata": {"modified_at": item_data["modified_at"], "hashes": item_data["hashes"]},
                }
                if existing:
                    for field, value in values.items():
                        setattr(existing, field, value)
                    existing.save()
                    intake_item = existing
                    results["updated"].append(item_data["book_code"])
                else:
                    intake_item = IntakeItem.objects.create(batch=batch, relative_path=item_data["relative_path"], **values)
                    results["created"].append(item_data["book_code"])
                promoted_item_ids.append(intake_item.id)
                _audit(batch=batch, item=intake_item, correlation_id=correlation_id, operation=intake_item.preview_operation, previous=previous, new="STAGED", attempt=intake_item.attempt_count, detail={"relative_path": intake_item.relative_path, "sha256": sha256})

            staged_by_code = {item_data["book_code"]: local_path for item_data, local_path, _sha in staged}

            def promote_after_commit():
                def promote_one(item_id):
                    close_old_connections()
                    item = IntakeItem.objects.select_related("batch").get(pk=item_id)
                    canonical = _canonical_path(storage, item.batch, {"book_code": item.book_code, "name": item.original_name})
                    try:
                        storage.promote_file(staged_by_code[item.book_code], canonical, item.sha256)
                        previous_status = item.status
                        item.status = "REGISTERED"
                        item.canonical_path = canonical
                        item.last_error = ""
                        item.save(update_fields=["status", "canonical_path", "last_error", "updated_at"])
                        _audit(batch=item.batch, item=item, correlation_id=correlation_id, operation="PROMOTE", previous=previous_status, new="REGISTERED", attempt=item.attempt_count, detail={"canonical_path": canonical, "sha256": item.sha256})
                    except (OSError, ValueError) as exc:
                        item.status = "FAILED_RETRYABLE"
                        item.last_error = str(exc)
                        item.save(update_fields=["status", "last_error", "updated_at"])
                        results["failed"].append(item.book_code)
                        _audit(batch=item.batch, item=item, correlation_id=correlation_id, operation="PROMOTE", previous="STAGED", new="FAILED_RETRYABLE", attempt=item.attempt_count, detail={"error": type(exc).__name__, "canonical_path": canonical})
                    finally:
                        close_old_connections()

                if promoted_item_ids:
                    with ThreadPoolExecutor(max_workers=min(6, len(promoted_item_ids))) as executor:
                        list(executor.map(promote_one, promoted_item_ids))

            transaction.on_commit(promote_after_commit)

        batch.refresh_from_db()
        pending = batch.items.filter(status__in=("STAGED", "FAILED_RETRYABLE")).count()
        conflicts_count = batch.items.filter(status="CONFLICT").count()
        batch.status = "FAILED_RETRYABLE" if pending else ("CONFLICT" if conflicts_count else "REGISTERED")
        batch.last_summary = {
            "correlation_id": correlation_id,
            "counts": {key: len(value) for key, value in results.items()},
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
        batch.last_error = "Um ou mais itens aguardam retry." if pending else ""
        batch.save(update_fields=["status", "last_summary", "last_error", "updated_at"])
        return {
            "status": batch.status,
            "batch_code": batch.batch_code,
            "source_path": batch.drive_source_path,
            "correlation_id": correlation_id,
            "counts": {key: len(value) for key, value in results.items()},
            "items": results,
        }


def retry_drive_batch(storage: DriveStoragePort, batch_code: str) -> dict:
    correlation_id = uuid.uuid4().hex
    retried, failed = [], []
    batch = IntakeBatch.objects.get(batch_code=batch_code, source="GOOGLE_DRIVE", remote=storage.remote)
    with storage.staging_directory() as staging_name:
        root = Path(staging_name)
        item_ids = list(batch.items.filter(status__in=("STAGED", "FAILED_RETRYABLE")).order_by("relative_path").values_list("id", flat=True))

        def retry_one(item_id):
            close_old_connections()
            item = IntakeItem.objects.select_related("batch").get(pk=item_id)
            target = root / f"{item.pk}{item.extension}"
            try:
                storage.download_to(item.remote_path, target)
                sha256 = _sha256_file(target)
                if sha256 != item.sha256:
                    raise IntakeValidationError("O arquivo remoto mudou antes do retry.")
                canonical = _canonical_path(storage, batch, {"book_code": item.book_code, "name": item.original_name})
                storage.promote_file(target, canonical, sha256)
                previous = item.status
                item.status = "REGISTERED"
                item.canonical_path = canonical
                item.last_error = ""
                item.attempt_count += 1
                item.save(update_fields=["status", "canonical_path", "last_error", "attempt_count", "updated_at"])
                retried.append(item.book_code)
                _audit(batch=batch, item=item, correlation_id=correlation_id, operation="RETRY", previous=previous, new="REGISTERED", attempt=item.attempt_count, detail={"canonical_path": canonical})
            except (OSError, ValueError) as exc:
                item.status = "FAILED_RETRYABLE"
                item.last_error = str(exc)
                item.attempt_count += 1
                item.save(update_fields=["status", "last_error", "attempt_count", "updated_at"])
                failed.append(item.book_code)
            finally:
                close_old_connections()

        if item_ids:
            with ThreadPoolExecutor(max_workers=min(6, len(item_ids))) as executor:
                list(executor.map(retry_one, item_ids))
    batch.status = "FAILED_RETRYABLE" if failed else "REGISTERED"
    batch.last_error = "Um ou mais itens aguardam retry." if failed else ""
    batch.last_summary = {"correlation_id": correlation_id, "retried": len(retried), "failed": len(failed)}
    batch.save(update_fields=["status", "last_error", "last_summary", "updated_at"])
    return {"status": batch.status, "batch_code": batch.batch_code, "retried": retried, "failed": failed, "correlation_id": correlation_id}
