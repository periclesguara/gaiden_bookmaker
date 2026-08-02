from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from django.conf import settings


class DriveStorageError(OSError):
    pass


class DrivePathError(ValueError):
    pass


def safe_drive_path(value: str, *, allow_empty: bool = False) -> str:
    supplied = str(value or "").replace("\\", "/").strip()
    if supplied.startswith("/"):
        raise DrivePathError("Caminho absoluto do Drive não é permitido.")
    raw = supplied.strip("/")
    if not raw and allow_empty:
        return ""
    if not raw or "\x00" in raw or any(ord(char) < 32 for char in raw):
        raise DrivePathError("Caminho do Drive vazio ou com caracteres de controle.")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise DrivePathError("Caminho do Drive inseguro.")
    return str(path)


class RcloneDriveStorage:
    """Infrastructure-only adapter constrained to the configured Drive root."""

    def __init__(self, *, remote: str | None = None, root: str | None = None):
        self.remote = (remote or settings.GAIDEN_DRIVE_REMOTE).strip().rstrip(":")
        if not self.remote or not all(char.isalnum() or char in "_.-" for char in self.remote):
            raise DrivePathError("Remote rclone não permitido.")
        self.root = safe_drive_path(
            settings.GAIDEN_DRIVE_ROOT if root is None else root,
            allow_empty=True,
        )
        self.inbox = safe_drive_path(settings.GAIDEN_DRIVE_INBOX)
        self.imported = safe_drive_path(settings.GAIDEN_DRIVE_IMPORTED)

    def _remote_path(self, relative_path: str) -> str:
        relative = safe_drive_path(relative_path, allow_empty=True)
        joined = "/".join(part for part in (self.root, relative) if part)
        return f"{self.remote}:{joined}"

    def source_path(self, folder: str) -> str:
        folder = safe_drive_path(folder, allow_empty=True)
        if folder == self.inbox or folder.startswith(f"{self.inbox}/"):
            result = folder
        else:
            result = f"{self.inbox}/{folder}" if folder else self.inbox
        if result != self.inbox and not result.startswith(f"{self.inbox}/"):
            raise DrivePathError("A pasta deve estar dentro de 01_INBOX_RAW.")
        return result

    @staticmethod
    def _run(args: list[str]) -> bytes:
        completed = subprocess.run(
            ["rclone", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise DriveStorageError(f"rclone falhou ({' '.join(args[:2])}): {detail}")
        return completed.stdout

    def list_folders(self, folder: str = "") -> list[dict]:
        source = self.source_path(folder)
        payload = json.loads(self._run(["lsjson", self._remote_path(source), "--dirs-only"]))
        return sorted(
            [
                {
                    "name": row.get("Name") or row.get("Path"),
                    "path": f"{source}/{row.get('Path') or row.get('Name')}",
                    "id": row.get("ID") or "",
                    "modified_at": row.get("ModTime") or "",
                }
                for row in payload
            ],
            key=lambda row: row["path"].casefold(),
        )

    def discover(self, folder: str, *, recursive: bool = True) -> tuple[str, list[dict]]:
        source = self.source_path(folder)
        args = ["lsjson", self._remote_path(source), "--files-only", "--hash"]
        if recursive:
            args.append("--recursive")
        payload = json.loads(self._run(args))
        rows = []
        for raw in payload:
            relative = safe_drive_path(raw.get("Path") or raw.get("Name"))
            rows.append(
                {
                    "remote_file_id": str(raw.get("ID") or ""),
                    "relative_path": relative,
                    "remote_path": f"{source}/{relative}",
                    "name": str(raw.get("Name") or PurePosixPath(relative).name),
                    "size": int(raw.get("Size") or 0),
                    "mime_type": str(raw.get("MimeType") or ""),
                    "modified_at": str(raw.get("ModTime") or ""),
                    "hashes": raw.get("Hashes") or {},
                    "is_link": bool(raw.get("IsLink")),
                }
            )
        return source, sorted(rows, key=lambda row: row["relative_path"].casefold())

    def download_to(self, remote_path: str, destination: Path) -> None:
        remote_path = self.source_path(remote_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._run(["copyto", self._remote_path(remote_path), str(destination)])

    def download_imported_to(self, canonical_path: str, destination: Path) -> None:
        canonical_path = safe_drive_path(canonical_path)
        if canonical_path != self.imported and not canonical_path.startswith(f"{self.imported}/"):
            raise DrivePathError("Arquivo selecionado fora de 02_IMPORTED_RAW.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._run(["copyto", self._remote_path(canonical_path), str(destination)])

    def promote_file(self, local_path: Path, canonical_path: str, expected_sha256: str) -> str:
        canonical_path = safe_drive_path(canonical_path)
        if canonical_path != self.imported and not canonical_path.startswith(f"{self.imported}/"):
            raise DrivePathError("Destino fora de 02_IMPORTED_RAW.")
        destination = self._remote_path(canonical_path)
        if self.exists(canonical_path):
            if self.sha256_remote(canonical_path) == expected_sha256:
                return "NO_OP"
            raise DriveStorageError("O destino canônico já contém bytes diferentes.")
        temp_path = f"{canonical_path}.gaiden-{uuid.uuid4().hex}.tmp"
        moved = False
        try:
            self._run(["copyto", str(local_path), self._remote_path(temp_path)])
            if self.sha256_remote(temp_path) != expected_sha256:
                raise DriveStorageError("SHA-256 remoto divergente antes da promoção.")
            self._run(["moveto", self._remote_path(temp_path), destination])
            moved = True
            if self.sha256_remote(canonical_path) != expected_sha256:
                raise DriveStorageError("SHA-256 remoto divergente após a promoção.")
            return "CREATE"
        finally:
            if not moved:
                subprocess.run(
                    ["rclone", "deletefile", self._remote_path(temp_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )

    def exists(self, relative_path: str) -> bool:
        completed = subprocess.run(
            ["rclone", "size", self._remote_path(relative_path), "--json"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return completed.returncode == 0

    def sha256_remote(self, relative_path: str) -> str:
        digest = hashlib.sha256()
        process = subprocess.Popen(
            ["rclone", "cat", self._remote_path(relative_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None
        for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
            digest.update(chunk)
        stderr = process.stderr.read() if process.stderr else b""
        if process.wait():
            raise DriveStorageError(stderr.decode("utf-8", errors="replace").strip())
        return digest.hexdigest()

    def staging_directory(self):
        return tempfile.TemporaryDirectory(prefix="gaiden-drive-intake-")
