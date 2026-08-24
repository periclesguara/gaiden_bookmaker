from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Protocol

from django.db import transaction
from django.utils import timezone

from pipeline.models import IncrementalBlock, IncrementalEdition, IncrementalImportEvent
from pipeline.services.incremental_import import canonical_manifest_sha256, resume_state, sha256_bytes


RCLONE_DESTINATION_RE = re.compile(r"^[A-Za-z0-9_.-]+:.+")


class Publisher(Protocol):
    def publish_bytes(self, relative_path: str, data: bytes) -> None: ...


def _safe_relative_path(relative_path: str) -> str:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Caminho relativo inseguro: {relative_path}")
    return str(path)


class FilesystemPublisher:
    def __init__(self, destination: str | Path):
        self.destination = Path(destination).expanduser().resolve()

    def publish_bytes(self, relative_path: str, data: bytes) -> None:
        relative_path = _safe_relative_path(relative_path)
        target = (self.destination / relative_path).resolve()
        target.relative_to(self.destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if temp.stat().st_size != len(data) or sha256_bytes(temp.read_bytes()) != sha256_bytes(data):
                raise OSError(f"Falha de validação antes de publicar {relative_path}.")
            os.replace(temp, target)
            if target.stat().st_size != len(data) or sha256_bytes(target.read_bytes()) != sha256_bytes(data):
                raise OSError(f"Falha de validação depois de publicar {relative_path}.")
        finally:
            if temp.exists():
                temp.unlink()


class RclonePublisher:
    """Atomic-ish publisher for Google Drive remotes configured in rclone."""

    def __init__(self, destination: str):
        if not RCLONE_DESTINATION_RE.fullmatch(destination) or "\n" in destination:
            raise ValueError("Destino rclone inválido.")
        self.destination = destination.rstrip("/")

    def _remote(self, relative_path: str) -> str:
        return f"{self.destination}/{_safe_relative_path(relative_path)}"

    @staticmethod
    def _run(*args: str, input_data: bytes | None = None) -> bytes:
        completed = subprocess.run(
            ["rclone", *args],
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise OSError(f"rclone falhou ({' '.join(args[:2])}): {detail}")
        return completed.stdout

    def publish_bytes(self, relative_path: str, data: bytes) -> None:
        final_remote = self._remote(relative_path)
        temp_remote = f"{final_remote}.gaiden-{uuid.uuid4().hex}.tmp"
        local_temp_name = ""
        try:
            with tempfile.NamedTemporaryFile(prefix="gaiden-incremental-", delete=False) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                local_temp_name = handle.name
            self._run("copyto", local_temp_name, temp_remote)
            remote_temp_data = self._run("cat", temp_remote)
            if len(remote_temp_data) != len(data) or sha256_bytes(remote_temp_data) != sha256_bytes(data):
                raise OSError(f"Validação remota falhou antes de publicar {relative_path}.")
            self._run("moveto", temp_remote, final_remote)
            remote_final_data = self._run("cat", final_remote)
            if len(remote_final_data) != len(data) or sha256_bytes(remote_final_data) != sha256_bytes(data):
                raise OSError(f"Validação remota falhou depois de publicar {relative_path}.")
        finally:
            if local_temp_name:
                Path(local_temp_name).unlink(missing_ok=True)
            if temp_remote:
                subprocess.run(
                    ["rclone", "deletefile", temp_remote],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )

    def read_bytes(self, relative_path: str) -> bytes:
        return self._run("cat", self._remote(relative_path))

    def stat(self, relative_path: str) -> dict[str, object] | None:
        remote = self.destination if not relative_path else self._remote(relative_path)
        completed = subprocess.run(
            ["rclone", "lsjson", remote, "--stat"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            if re.search(r"not found|directory not found|object not found", detail, re.IGNORECASE):
                return None
            raise OSError(f"rclone falhou (lsjson --stat): {detail}")
        payload = json.loads(completed.stdout or b"null")
        return payload if isinstance(payload, dict) else None

    def list_files(self, relative_directory: str) -> list[dict[str, object]]:
        remote = self._remote(relative_directory)
        payload = json.loads(self._run("lsjson", remote, "--files-only") or b"[]")
        if not isinstance(payload, list):
            raise OSError("Resposta inesperada ao listar arquivos do Drive.")
        return [row for row in payload if isinstance(row, dict)]


def publisher_for(destination: str | Path) -> Publisher:
    value = str(destination).strip()
    if not value:
        raise ValueError("Informe o destino de exportação.")
    if RCLONE_DESTINATION_RE.fullmatch(value):
        return RclonePublisher(value)
    return FilesystemPublisher(value)


def _json_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _export_manifest(edition: IncrementalEdition, current_blocks: list[IncrementalBlock]) -> dict:
    latest_run = edition.import_runs.order_by("-started_at").first()
    blocks = [
        {
            "sequence": block.sequence,
            "block_id": block.block_id,
            "file_name": block.file_name,
            "content_sha256": block.content_sha256,
            "size_bytes": block.size_bytes,
            "status": block.status,
            "version": block.version,
            "source_block_id": block.source_block_id or None,
            "updated_at": block.updated_at.isoformat(),
        }
        for block in current_blocks
    ]
    payload = {
        "schema_version": 1,
        "job_id": latest_run.job_id if latest_run else f"{edition.edition_id}-incremental",
        "work_id": edition.work_id,
        "edition_id": edition.edition_id,
        "book_code": edition.book_code,
        "locale": edition.locale,
        "status": edition.status,
        "expected_block_count": edition.expected_block_count,
        "last_contiguous_sequence": edition.last_contiguous_sequence,
        "next_sequence": edition.next_sequence,
        "generated_at": timezone.now().isoformat(),
        "blocks": blocks,
    }
    payload["manifest_sha256"] = canonical_manifest_sha256(payload)
    return payload


def export_changed_blocks(
    edition_id: str,
    destination: str | Path,
    *,
    after_sequence: int | None = None,
    publisher: Publisher | None = None,
) -> dict:
    edition = IncrementalEdition.objects.get(edition_id=edition_id)
    current_blocks = list(edition.blocks.filter(is_current=True).order_by("sequence"))
    changed = [
        block
        for block in current_blocks
        if (after_sequence is None or block.sequence > after_sequence)
        and (block.exported_sha256 != block.content_sha256 or block.exported_status != block.status)
    ]
    active_publisher = publisher or publisher_for(destination)

    for block in changed:
        data = block.content.encode("utf-8")
        if len(data) != block.size_bytes or sha256_bytes(data) != block.content_sha256:
            raise ValueError(f"Conteúdo persistido inválido para {block.block_id}.")
        active_publisher.publish_bytes(f"blocks/{block.file_name}", data)

    conflict_rows = list(
        IncrementalImportEvent.objects.filter(run__edition=edition, action__in=("CONFLICT", "FAILED"))
        .order_by("sequence", "created_at")
        .values("sequence", "block_id", "action", "detail", "created_at")
    )
    for row in conflict_rows:
        row["created_at"] = row["created_at"].isoformat()
    manifest = _export_manifest(edition, current_blocks)
    state = resume_state(edition_id)
    state["manifest_sha256"] = manifest["manifest_sha256"]
    latest_run = edition.import_runs.order_by("-started_at").first()
    ack = {
        "edition_id": edition.edition_id,
        "last_import_run_id": edition.last_import_run_id,
        "manifest_sha256": manifest["manifest_sha256"],
        "exported_sequences": [block.sequence for block in changed],
        "resume_state": state,
        "import_result": latest_run.result if latest_run else {},
        "published_at": timezone.now().isoformat(),
    }

    # import-ack.json is intentionally the final publication operation.
    active_publisher.publish_bytes("control/resume-state.json", _json_bytes(state))
    active_publisher.publish_bytes("control/errors.json", _json_bytes({"errors": conflict_rows}))
    active_publisher.publish_bytes("control/manifest.json", _json_bytes(manifest))
    active_publisher.publish_bytes("control/import-ack.json", _json_bytes(ack))

    published_at = timezone.now()
    with transaction.atomic():
        IncrementalEdition.objects.filter(pk=edition.pk).update(
            drive_destination=str(destination),
            manifest_sha256=manifest["manifest_sha256"],
            updated_at=published_at,
        )
        IncrementalBlock.objects.filter(pk__in=[block.pk for block in changed]).update(
            exported_at=published_at,
        )
        for block in changed:
            IncrementalBlock.objects.filter(pk=block.pk).update(
                exported_sha256=block.content_sha256,
                exported_status=block.status,
            )
    return ack
