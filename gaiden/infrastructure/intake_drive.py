from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


DEFAULT_REMOTE = "gaiden_drive:"
DEFAULT_INBOX = "01_INBOX_RAW"


class RcloneUnavailableError(RuntimeError):
    pass


class RcloneCommandError(RuntimeError):
    pass


@dataclass(frozen=True)
class DriveFile:
    file_id: str
    name: str
    relative_path: str
    size: int
    mime_type: str = ""


def _safe_relative_path(value: str) -> str:
    candidate = PurePosixPath((value or "").strip())
    if not value or candidate.is_absolute() or ".." in candidate.parts or ":" in value or "\\" in value:
        raise ValueError(f"Unsafe Drive path: {value!r}")
    return candidate.as_posix().strip("/")


class RcloneClient:
    def __init__(self, *, timeout: int = 60):
        self.remote = (os.environ.get("GAIDEN_INTAKE_RCLONE_REMOTE") or DEFAULT_REMOTE).strip()
        self.inbox = _safe_relative_path(os.environ.get("GAIDEN_INTAKE_DRIVE_INBOX") or DEFAULT_INBOX)
        self.timeout = timeout
        if not self.remote.endswith(":") or "/" in self.remote:
            raise ValueError("GAIDEN_INTAKE_RCLONE_REMOTE must be an rclone remote ending in ':'")

    def check_available(self) -> None:
        if not shutil.which("rclone"):
            raise RcloneUnavailableError("rclone executable is not available")
        self._run(["rclone", "version"])

    @property
    def executable_available(self) -> bool:
        return bool(shutil.which("rclone"))

    def list_folders(self, relative_path: str = "") -> list[str]:
        target = self._remote_path(relative_path)
        result = self._run(["rclone", "lsf", target, "--dirs-only"])
        return [line.strip().rstrip("/") for line in result.stdout.splitlines() if line.strip()]

    def list_files(self, relative_path: str) -> list[DriveFile]:
        target = self._remote_path(relative_path)
        result = self._run(["rclone", "lsjson", target, "--files-only"])
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RcloneCommandError("rclone returned invalid JSON") from exc
        files: list[DriveFile] = []
        for row in payload:
            name = Path(row.get("Name") or row.get("Path") or "").name
            if not name:
                continue
            files.append(
                DriveFile(
                    file_id=str(row.get("ID") or ""),
                    name=name,
                    relative_path=str(row.get("Path") or name),
                    size=max(0, int(row.get("Size") or 0)),
                    mime_type=str(row.get("MimeType") or ""),
                )
            )
        return files

    def download_file(self, folder: str, drive_file: DriveFile, destination: Path) -> Path:
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"Download destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = self._remote_path(f"{_safe_relative_path(folder)}/{_safe_relative_path(drive_file.relative_path)}")
        self._run(["rclone", "copyto", source, str(destination), "--no-traverse"])
        if not destination.is_file() or destination.is_symlink():
            raise RcloneCommandError("rclone did not produce a regular downloaded file")
        return destination

    def _remote_path(self, relative_path: str) -> str:
        parts = [self.inbox]
        if relative_path:
            parts.append(_safe_relative_path(relative_path))
        return f"{self.remote}{'/'.join(parts)}"

    def _run(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                arguments,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RcloneCommandError(f"rclone timed out after {self.timeout}s") from exc
        if result.returncode != 0:
            safe_error = (result.stderr or "rclone command failed").strip().replace("\n", " ")[:500]
            raise RcloneCommandError(f"rclone failed with exit {result.returncode}: {safe_error}")
        return result
