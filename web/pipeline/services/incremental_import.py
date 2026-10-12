from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from editorial.models import Edition as EditorialEdition
from pipeline.models import (
    IncrementalBlock,
    IncrementalEdition,
    IncrementalImportEvent,
    IncrementalImportRun,
)


TOP_LEVEL_REQUIRED = {
    "schema_version",
    "job_id",
    "work_id",
    "edition_id",
    "book_code",
    "locale",
    "status",
    "expected_block_count",
    "last_contiguous_sequence",
    "next_sequence",
    "blocks",
}
TOP_LEVEL_ALLOWED = TOP_LEVEL_REQUIRED | {"manifest_sha256", "generated_at"}
BLOCK_REQUIRED = {
    "sequence",
    "block_id",
    "file_name",
    "content_sha256",
    "size_bytes",
    "status",
    "version",
}
BLOCK_ALLOWED = BLOCK_REQUIRED | {"source_block_id", "updated_at"}
MANIFEST_STATUSES = {
    "DRAFT",
    "READY",
    "IMPORTED",
    "IN_PROGRESS",
    "RETURNED",
    "APPROVED",
    "FAILED",
}
BLOCK_STATUSES = MANIFEST_STATUSES | {"SUPERSEDED"}
CONFIRMED_STATUSES = {"READY", "IMPORTED", "IN_PROGRESS", "RETURNED", "APPROVED"}
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
BOOK_CODE_RE = re.compile(r"^book_[0-9]{4,}$")
LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


class ManifestValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class ImportRunConflict(RuntimeError):
    pass


@dataclass
class PreparedBlock:
    manifest: dict[str, Any]
    path: Path
    data: bytes
    content: str


@dataclass
class PreviewResult:
    manifest: dict[str, Any]
    manifest_sha256: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    current_last_contiguous_sequence: int = 0
    current_next_sequence: int = 1

    @property
    def found_count(self) -> int:
        return sum(1 for row in self.rows if not row.get("error"))

    @property
    def can_import(self) -> bool:
        return not self.errors and all(not row.get("error") for row in self.rows)

    @property
    def batch_start(self) -> int | None:
        return self.rows[0]["sequence"] if self.rows else None

    @property
    def batch_end(self) -> int | None:
        return self.rows[-1]["sequence"] if self.rows else None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_manifest_sha256(payload: dict[str, Any]) -> str:
    canonical_payload = dict(payload)
    canonical_payload.pop("manifest_sha256", None)
    canonical = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(canonical)


def load_manifest(manifest_path: str | Path) -> dict[str, Any]:
    path = Path(manifest_path).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestValidationError([f"Manifesto não encontrado: {path}"]) from exc
    except UnicodeDecodeError as exc:
        raise ManifestValidationError(["O manifesto deve usar encoding UTF-8."]) from exc
    except json.JSONDecodeError as exc:
        raise ManifestValidationError([f"JSON inválido: linha {exc.lineno}, coluna {exc.colno}."]) from exc
    validate_manifest(payload)
    return payload


def validate_manifest(payload: Any) -> None:
    errors: list[str] = []
    if not isinstance(payload, dict):
        raise ManifestValidationError(["A raiz do manifesto deve ser um objeto JSON."])

    missing = sorted(TOP_LEVEL_REQUIRED - set(payload))
    unexpected = sorted(set(payload) - TOP_LEVEL_ALLOWED)
    if missing:
        errors.append("Campos obrigatórios ausentes: " + ", ".join(missing))
    if unexpected:
        errors.append("Campos não permitidos: " + ", ".join(unexpected))

    if payload.get("schema_version") != 1:
        errors.append("schema_version deve ser 1.")
    for name in ("job_id", "work_id", "edition_id"):
        if not isinstance(payload.get(name), str) or not payload.get(name):
            errors.append(f"{name} deve ser uma string não vazia.")
    if not isinstance(payload.get("book_code"), str) or not BOOK_CODE_RE.fullmatch(payload.get("book_code", "")):
        errors.append("book_code deve seguir o padrão book_0000.")
    if not isinstance(payload.get("locale"), str) or not LOCALE_RE.fullmatch(payload.get("locale", "")):
        errors.append("locale deve seguir o padrão ll-CC, por exemplo pt-BR.")
    if payload.get("status") not in MANIFEST_STATUSES:
        errors.append("status geral inválido.")

    expected = payload.get("expected_block_count")
    last = payload.get("last_contiguous_sequence")
    next_sequence = payload.get("next_sequence")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
        errors.append("expected_block_count deve ser um inteiro positivo.")
    if not isinstance(last, int) or isinstance(last, bool) or last < 0:
        errors.append("last_contiguous_sequence deve ser um inteiro não negativo.")
    if next_sequence is not None and (
        not isinstance(next_sequence, int) or isinstance(next_sequence, bool) or next_sequence < 1
    ):
        errors.append("next_sequence deve ser nulo ou um inteiro positivo.")
    if isinstance(expected, int) and isinstance(last, int) and last > expected:
        errors.append("last_contiguous_sequence não pode exceder expected_block_count.")
    if isinstance(expected, int) and isinstance(next_sequence, int) and next_sequence > expected + 1:
        errors.append("next_sequence não pode exceder expected_block_count + 1.")

    manifest_hash = payload.get("manifest_sha256")
    if manifest_hash is not None:
        if not isinstance(manifest_hash, str) or not SHA256_RE.fullmatch(manifest_hash):
            errors.append("manifest_sha256 deve ser um SHA-256 hexadecimal minúsculo.")
        elif manifest_hash != canonical_manifest_sha256(payload):
            errors.append("manifest_sha256 não corresponde ao conteúdo canônico do manifesto.")

    generated_at = payload.get("generated_at")
    if generated_at is not None and (not isinstance(generated_at, str) or parse_datetime(generated_at) is None):
        errors.append("generated_at deve usar o formato date-time ISO 8601.")

    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        errors.append("blocks deve ser uma lista.")
        blocks = []
    seen_sequences: set[int] = set()
    seen_ids: set[str] = set()
    previous_sequence = 0
    edition_id = payload.get("edition_id", "")
    for index, block in enumerate(blocks, start=1):
        prefix = f"blocks[{index - 1}]"
        if not isinstance(block, dict):
            errors.append(f"{prefix} deve ser um objeto.")
            continue
        block_missing = sorted(BLOCK_REQUIRED - set(block))
        block_unexpected = sorted(set(block) - BLOCK_ALLOWED)
        if block_missing:
            errors.append(f"{prefix}: campos ausentes: {', '.join(block_missing)}")
        if block_unexpected:
            errors.append(f"{prefix}: campos não permitidos: {', '.join(block_unexpected)}")
        sequence = block.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            errors.append(f"{prefix}.sequence deve ser um inteiro positivo.")
        else:
            if sequence in seen_sequences:
                errors.append(f"Sequência duplicada: {sequence}.")
            if sequence <= previous_sequence:
                errors.append("A lista de blocos deve estar em sequence crescente.")
            if isinstance(expected, int) and sequence > expected:
                errors.append(f"Sequência {sequence} excede expected_block_count.")
            seen_sequences.add(sequence)
            previous_sequence = sequence
        block_id = block.get("block_id")
        if not isinstance(block_id, str) or not block_id:
            errors.append(f"{prefix}.block_id deve ser uma string não vazia.")
        else:
            if edition_id and not block_id.startswith(f"{edition_id}:"):
                errors.append(f"{prefix}.block_id não pertence à edição declarada.")
            if block_id in seen_ids:
                errors.append(f"block_id duplicado: {block_id}.")
            seen_ids.add(block_id)
        file_name = block.get("file_name")
        if not isinstance(file_name, str) or not file_name or Path(file_name).name != file_name:
            errors.append(f"{prefix}.file_name deve ser apenas um nome de arquivo seguro.")
        block_hash = block.get("content_sha256")
        if not isinstance(block_hash, str) or not SHA256_RE.fullmatch(block_hash):
            errors.append(f"{prefix}.content_sha256 inválido.")
        size = block.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            errors.append(f"{prefix}.size_bytes deve ser um inteiro positivo.")
        if block.get("status") not in BLOCK_STATUSES:
            errors.append(f"{prefix}.status inválido.")
        version = block.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            errors.append(f"{prefix}.version deve ser um inteiro positivo.")
        source_block_id = block.get("source_block_id")
        if source_block_id is not None and not isinstance(source_block_id, str):
            errors.append(f"{prefix}.source_block_id deve ser string ou nulo.")
        updated_at = block.get("updated_at")
        if updated_at is not None and (not isinstance(updated_at, str) or parse_datetime(updated_at) is None):
            errors.append(f"{prefix}.updated_at deve usar o formato date-time ISO 8601.")

    if errors:
        raise ManifestValidationError(errors)


def _block_search_roots(manifest_path: Path, blocks_directory: str | Path | None) -> list[Path]:
    roots: list[Path] = []
    if blocks_directory:
        roots.append(Path(blocks_directory).expanduser().resolve())
    roots.extend([manifest_path.parent, manifest_path.parent / "blocks", manifest_path.parent.parent / "blocks"])
    unique: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def prepare_block(
    block_manifest: dict[str, Any],
    *,
    manifest_path: str | Path,
    blocks_directory: str | Path | None = None,
) -> PreparedBlock:
    manifest_path = Path(manifest_path).expanduser().resolve()
    file_name = block_manifest["file_name"]
    path = None
    for root in _block_search_roots(manifest_path, blocks_directory):
        candidate = root / file_name
        if candidate.is_symlink():
            raise ValueError(
                f"Symlink não permitido na sequência {block_manifest['sequence']}: {file_name}"
            )
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"Arquivo fora da raiz permitida na sequência {block_manifest['sequence']}: {file_name}"
            ) from exc
        path = resolved
        break
    if path is None:
        raise ValueError(f"Arquivo ausente para a sequência {block_manifest['sequence']}: {file_name}")
    data = path.read_bytes()
    if len(data) != block_manifest["size_bytes"]:
        raise ValueError(
            f"Tamanho divergente na sequência {block_manifest['sequence']}: "
            f"manifesto={block_manifest['size_bytes']}, arquivo={len(data)}"
        )
    actual_hash = sha256_bytes(data)
    if actual_hash != block_manifest["content_sha256"]:
        raise ValueError(
            f"SHA-256 divergente na sequência {block_manifest['sequence']}: "
            f"manifesto={block_manifest['content_sha256']}, arquivo={actual_hash}"
        )
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Encoding inválido na sequência {block_manifest['sequence']}; esperado UTF-8.") from exc
    if not content.strip():
        raise ValueError(f"Conteúdo vazio na sequência {block_manifest['sequence']}.")
    if "\x00" in content:
        raise ValueError(f"Byte NUL não permitido na sequência {block_manifest['sequence']}.")
    return PreparedBlock(block_manifest, path, data, content)


def _find_editorial_edition(book_code: str, locale: str) -> EditorialEdition | None:
    locale_lower = locale.lower()
    candidates = {locale_lower, locale_lower.replace("-", ""), locale_lower.split("-")[0]}
    return (
        EditorialEdition.objects.filter(work__code=book_code, language__code__in=candidates)
        .order_by("id")
        .first()
    )


def _current_blocks(edition: IncrementalEdition) -> dict[str, IncrementalBlock]:
    return {
        block.block_id: block
        for block in edition.blocks.filter(is_current=True).order_by("sequence")
    }


def _classify(block_manifest: dict[str, Any], current_by_id: dict[str, IncrementalBlock], current_by_sequence: dict[int, IncrementalBlock]) -> tuple[str, str]:
    current = current_by_id.get(block_manifest["block_id"])
    sequence_owner = current_by_sequence.get(block_manifest["sequence"])
    if sequence_owner is not None and sequence_owner.block_id != block_manifest["block_id"]:
        return "CONFLICT", f"A sequência já pertence a {sequence_owner.block_id}."
    if current is None:
        return "CREATE", "Nova identidade de bloco."
    if current.sequence != block_manifest["sequence"]:
        return "CONFLICT", f"O bloco já está registrado na sequência {current.sequence}."
    if current.content_sha256 == block_manifest["content_sha256"]:
        return "NOOP", "Conteúdo já importado; eventual avanço de estado editorial será sincronizado."
    if block_manifest["version"] > current.version:
        return "UPDATE", f"Criará a versão {block_manifest['version']} e preservará a versão {current.version}."
    return "CONFLICT", "Hash diferente sem incremento de versão."


def preview_manifest(
    manifest_path: str | Path,
    *,
    blocks_directory: str | Path | None = None,
) -> PreviewResult:
    path = Path(manifest_path).expanduser().resolve()
    payload = load_manifest(path)
    manifest_hash = canonical_manifest_sha256(payload)
    result = PreviewResult(payload, manifest_hash)
    edition = IncrementalEdition.objects.filter(edition_id=payload["edition_id"]).first()
    current_by_id = _current_blocks(edition) if edition else {}
    current_by_sequence = {block.sequence: block for block in current_by_id.values()}
    if edition:
        result.current_last_contiguous_sequence = edition.last_contiguous_sequence
        result.current_next_sequence = edition.next_sequence or (edition.expected_block_count + 1)
    for block_manifest in payload["blocks"]:
        row = dict(block_manifest)
        try:
            prepare_block(block_manifest, manifest_path=path, blocks_directory=blocks_directory)
            row["action"], row["detail"] = _classify(block_manifest, current_by_id, current_by_sequence)
            row["error"] = ""
        except (OSError, ValueError) as exc:
            row["action"] = "CONFLICT"
            row["detail"] = str(exc)
            row["error"] = str(exc)
        result.rows.append(row)
    return result


def _advance_status(current_status: str, incoming_status: str) -> str:
    if incoming_status in {"FAILED", "SUPERSEDED"}:
        return incoming_status
    order = ["DRAFT", "READY", "IMPORTED", "IN_PROGRESS", "RETURNED", "APPROVED"]
    try:
        return incoming_status if order.index(incoming_status) > order.index(current_status) else current_status
    except ValueError:
        return current_status


def _resume_values(edition: IncrementalEdition) -> tuple[int, int | None, str, list[int], bool]:
    current = list(edition.blocks.filter(is_current=True).order_by("sequence"))
    confirmed = {block.sequence: block for block in current if block.status in CONFIRMED_STATUSES}
    contiguous = 0
    while contiguous + 1 in confirmed:
        contiguous += 1
    complete_prefix = contiguous >= edition.expected_block_count
    next_sequence = None if complete_prefix else contiguous + 1
    confirmed_block_id = confirmed[contiguous].block_id if contiguous else ""
    highest_present = max((block.sequence for block in current), default=0)
    gaps = [sequence for sequence in range(1, highest_present + 1) if sequence not in confirmed]
    all_approved = (
        complete_prefix
        and len(current) == edition.expected_block_count
        and all(block.status == "APPROVED" for block in current)
    )
    return contiguous, next_sequence, confirmed_block_id, gaps, all_approved


def _persist_resume_state(edition: IncrementalEdition, *, manifest_hash: str, run_id: str) -> dict[str, Any]:
    contiguous, next_sequence, confirmed_block_id, gaps, all_approved = _resume_values(edition)
    edition.last_contiguous_sequence = contiguous
    edition.next_sequence = next_sequence
    edition.confirmed_block_id = confirmed_block_id
    edition.manifest_sha256 = manifest_hash
    edition.last_import_run_id = run_id
    edition.status = "APPROVED" if all_approved else ("IN_PROGRESS" if edition.blocks.filter(is_current=True).exists() else "DRAFT")
    edition.save(
        update_fields=[
            "last_contiguous_sequence",
            "next_sequence",
            "confirmed_block_id",
            "manifest_sha256",
            "last_import_run_id",
            "status",
            "updated_at",
        ]
    )
    return {
        "last_contiguous_sequence": contiguous,
        "next_sequence": next_sequence,
        "confirmed_block_id": confirmed_block_id,
        "gaps": gaps,
        "status": edition.status,
    }


def _event(run: IncrementalImportRun, block_manifest: dict[str, Any], action: str, *, block: IncrementalBlock | None = None, detail: dict[str, Any] | None = None) -> None:
    IncrementalImportEvent.objects.create(
        run=run,
        block_version=block,
        sequence=block_manifest["sequence"],
        block_id=block_manifest["block_id"],
        action=action,
        detail=detail or {},
    )


def _process_one_block(run: IncrementalImportRun, prepared: PreparedBlock) -> tuple[str, dict[str, Any]]:
    incoming = prepared.manifest
    with transaction.atomic():
        edition = IncrementalEdition.objects.select_for_update().get(pk=run.edition_id)
        current = (
            IncrementalBlock.objects.select_for_update()
            .filter(edition=edition, block_id=incoming["block_id"], is_current=True)
            .first()
        )
        sequence_owner = (
            IncrementalBlock.objects.select_for_update()
            .filter(edition=edition, sequence=incoming["sequence"], is_current=True)
            .first()
        )
        if sequence_owner is not None and sequence_owner.block_id != incoming["block_id"]:
            detail = {"reason": "SEQUENCE_OWNED_BY_OTHER_BLOCK", "owner": sequence_owner.block_id}
            _event(run, incoming, "CONFLICT", detail=detail)
            return "conflict", detail
        if current is not None and current.sequence != incoming["sequence"]:
            detail = {"reason": "BLOCK_SEQUENCE_CHANGED", "stored_sequence": current.sequence}
            _event(run, incoming, "CONFLICT", block=current, detail=detail)
            return "conflict", detail
        if current is not None and current.content_sha256 == incoming["content_sha256"]:
            next_status = _advance_status(current.status, incoming["status"])
            status_changed = next_status != current.status
            if status_changed:
                current.status = next_status
                current.save(update_fields=["status", "updated_at"])
            detail = {"status_changed": status_changed, "status": current.status}
            _event(run, incoming, "NOOP_ALREADY_IMPORTED", block=current, detail=detail)
            return "noop", detail
        if current is not None and incoming["version"] <= current.version:
            detail = {
                "reason": "HASH_CHANGED_WITHOUT_VERSION_INCREMENT",
                "stored_version": current.version,
                "received_version": incoming["version"],
                "stored_sha256": current.content_sha256,
                "received_sha256": incoming["content_sha256"],
            }
            _event(run, incoming, "CONFLICT", block=current, detail=detail)
            return "conflict", detail

        action = "created"
        if current is not None:
            current.is_current = False
            current.status = "SUPERSEDED"
            current.save(update_fields=["is_current", "status", "updated_at"])
            action = "updated"
        block = IncrementalBlock.objects.create(
            edition=edition,
            block_id=incoming["block_id"],
            sequence=incoming["sequence"],
            version=incoming["version"],
            file_name=incoming["file_name"],
            content=prepared.content,
            content_sha256=incoming["content_sha256"],
            size_bytes=incoming["size_bytes"],
            status=incoming["status"],
            source_block_id=incoming.get("source_block_id") or "",
            source_updated_at=parse_datetime(incoming["updated_at"]) if incoming.get("updated_at") else None,
            is_current=True,
        )
        event_action = "CREATE" if action == "created" else "CREATE_NEW_VERSION"
        _event(run, incoming, event_action, block=block, detail={"version": block.version})
        return action, {"version": block.version}


def import_manifest(
    manifest_path: str | Path,
    *,
    blocks_directory: str | Path | None = None,
    stop_on_conflict: bool = True,
    import_attempt: int = 1,
    failure_injector: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    path = Path(manifest_path).expanduser().resolve()
    payload = load_manifest(path)
    manifest_hash = canonical_manifest_sha256(payload)
    if import_attempt < 1:
        raise ValueError("import_attempt deve ser positivo.")

    with transaction.atomic():
        edition, created = IncrementalEdition.objects.select_for_update().get_or_create(
            edition_id=payload["edition_id"],
            defaults={
                "editorial_edition": _find_editorial_edition(payload["book_code"], payload["locale"]),
                "work_id": payload["work_id"],
                "book_code": payload["book_code"],
                "locale": payload["locale"],
                "expected_block_count": payload["expected_block_count"],
                "status": payload["status"],
                "next_sequence": 1,
            },
        )
        if not created:
            immutable = {
                "work_id": payload["work_id"],
                "book_code": payload["book_code"],
                "locale": payload["locale"],
                "expected_block_count": payload["expected_block_count"],
            }
            mismatches = [name for name, value in immutable.items() if getattr(edition, name) != value]
            if mismatches:
                raise ManifestValidationError(["Identidade da edição divergente: " + ", ".join(mismatches)])
        try:
            with transaction.atomic():
                run = IncrementalImportRun.objects.create(
                    run_id=uuid.uuid4().hex,
                    edition=edition,
                    job_id=payload["job_id"],
                    manifest_sha256=manifest_hash,
                    import_attempt=import_attempt,
                    manifest=payload,
                )
        except IntegrityError as exc:
            previous = IncrementalImportRun.objects.filter(
                job_id=payload["job_id"],
                manifest_sha256=manifest_hash,
                import_attempt=import_attempt,
            ).first()
            if previous and previous.status in {"SUCCESS", "PARTIAL"}:
                replay = dict(previous.result)
                replay["replayed_import_run"] = previous.run_id
                return replay
            raise ImportRunConflict(
                "Já existe uma execução ativa ou falha para esta chave de idempotência; incremente import_attempt."
            ) from exc

    result: dict[str, Any] = {
        "run_id": run.run_id,
        "edition_id": payload["edition_id"],
        "manifest_sha256": manifest_hash,
        "created": [],
        "updated": [],
        "noop": [],
        "conflicts": [],
        "failed": [],
    }
    for block_manifest in payload["blocks"]:
        try:
            if failure_injector is not None:
                failure_injector(block_manifest["sequence"])
            prepared = prepare_block(block_manifest, manifest_path=path, blocks_directory=blocks_directory)
            action, detail = _process_one_block(run, prepared)
            if action in {"created", "updated", "noop"}:
                result[action].append(block_manifest["sequence"])
            else:
                result["conflicts"].append(
                    {"sequence": block_manifest["sequence"], "block_id": block_manifest["block_id"], **detail}
                )
                if stop_on_conflict:
                    break
        except Exception as exc:
            with transaction.atomic():
                _event(run, block_manifest, "FAILED", detail={"error": str(exc)})
            result["failed"].append(
                {"sequence": block_manifest["sequence"], "block_id": block_manifest["block_id"], "error": str(exc)}
            )
            break

    with transaction.atomic():
        locked_edition = IncrementalEdition.objects.select_for_update().get(pk=edition.pk)
        resume = _persist_resume_state(locked_edition, manifest_hash=manifest_hash, run_id=run.run_id)
        result.update(resume)
        run = IncrementalImportRun.objects.select_for_update().get(pk=run.pk)
        has_problems = bool(result["conflicts"] or result["failed"])
        has_success = bool(result["created"] or result["updated"] or result["noop"])
        run.status = "PARTIAL" if has_problems and has_success else ("FAILED" if has_problems else "SUCCESS")
        run.result = result
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "result", "completed_at"])
    return result


def resume_state(edition_id: str) -> dict[str, Any]:
    edition = IncrementalEdition.objects.get(edition_id=edition_id)
    conflicts = list(
        IncrementalImportEvent.objects.filter(run__edition=edition, action="CONFLICT")
        .order_by("sequence")
        .values("sequence", "block_id", "detail")
    )
    _, _, _, gaps, _ = _resume_values(edition)
    return {
        "edition_id": edition.edition_id,
        "last_contiguous_sequence": edition.last_contiguous_sequence,
        "next_sequence": edition.next_sequence,
        "confirmed_block_id": edition.confirmed_block_id,
        "manifest_sha256": edition.manifest_sha256,
        "updated_at": edition.updated_at.isoformat(),
        "last_import_run_id": edition.last_import_run_id,
        "conflicts": conflicts,
        "gaps": gaps,
        "status": edition.status,
    }
