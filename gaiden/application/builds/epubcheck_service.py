"""Official EPUBCheck gate for final, publishable EPUB artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

from gaiden.infrastructure import storage


STATUS_PENDING = "EPUBCHECK_PENDING"
STATUS_RUNNING = "EPUBCHECK_RUNNING"
STATUS_PASSED = "EPUBCHECK_PASSED"
STATUS_PASSED_WITH_WARNINGS = "EPUBCHECK_PASSED_WITH_WARNINGS"
STATUS_FAILED = "EPUBCHECK_FAILED"
STATUS_UNAVAILABLE = "EPUBCHECK_UNAVAILABLE"

_COUNTS_RE = re.compile(
    r"Messages:\s*(\d+)\s+fatals?\s*/\s*(\d+)\s+errors?\s*/\s*(\d+)\s+warnings?",
    re.IGNORECASE,
)


def stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_epub_path(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    allowed_roots = (storage.storage_root().resolve(), storage.repo_root().resolve())
    if (
        not path.is_file()
        or path.suffix.casefold() != ".epub"
        or not any(path.is_relative_to(root) for root in allowed_roots)
    ):
        raise ValueError("EPUBCheck accepts only existing .epub files in canonical storage.")
    return path


def _counts(output: str) -> tuple[int, int, int]:
    match = _COUNTS_RE.search(output)
    return tuple(map(int, match.groups())) if match else (0, 0, 0)


def installed_epubcheck_version() -> str | None:
    """Return the provisioned validator version, or ``None`` when unavailable."""
    if not getattr(settings, "EPUBCHECK_ENABLED", True):
        return None
    executable = str(getattr(settings, "EPUBCHECK_EXECUTABLE", "epubcheck"))
    resolved = shutil.which(executable)
    if not resolved:
        return None
    try:
        result = subprocess.run(
            [resolved, "--version"], capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return ((result.stdout or result.stderr).strip().splitlines() or ["unknown"])[0][:200]


def _report(
    *,
    path: Path,
    started_at: datetime,
    status: str,
    version: str,
    returncode: int | None,
    stdout: str = "",
    stderr: str = "",
) -> dict[str, object]:
    completed_at = _now()
    fatal_count, error_count, warning_count = _counts(f"{stdout}\n{stderr}")
    return {
        "schema": "gaiden_epubcheck_report_v1",
        "tool": "EPUBCheck",
        "tool_version": version,
        "status": status,
        "passed": status in {STATUS_PASSED, STATUS_PASSED_WITH_WARNINGS},
        "epub_path": str(path),
        "epub_sha256": stream_sha256(path),
        "returncode": returncode,
        "fatal_count": fatal_count,
        "error_count": error_count,
        "warning_count": warning_count,
        "stdout": stdout,
        "stderr": stderr,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": round((completed_at - started_at).total_seconds(), 3),
    }


def validate_epubcheck(path: str | Path, *, skip_for_tests: bool = False) -> dict[str, object]:
    """Run the configured official EPUBCheck executable against the final file.

    Production never skips this call. The isolated test settings must opt in to
    the synthetic branch explicitly, so a missing executable always fails closed
    in an operator environment.
    """
    epub_path = _safe_epub_path(path)
    started_at = _now()
    if skip_for_tests:
        if not getattr(settings, "GAIDEN_ALLOW_EPUBCHECK_SKIP_FOR_TESTS", False):
            raise ValueError("EPUBCheck can only be skipped by isolated test settings.")
        return _report(
            path=epub_path,
            started_at=started_at,
            status=STATUS_PASSED,
            version="TEST_SKIP",
            returncode=0,
            stdout="EPUBCheck explicitly skipped by isolated test settings.",
        )

    if not getattr(settings, "EPUBCHECK_ENABLED", True):
        return _report(
            path=epub_path,
            started_at=started_at,
            status=STATUS_UNAVAILABLE,
            version="disabled",
            returncode=None,
            stderr="EPUBCheck is unavailable in the current environment.",
        )
    executable = str(getattr(settings, "EPUBCHECK_EXECUTABLE", "epubcheck"))
    resolved = shutil.which(executable)
    if not resolved:
        return _report(
            path=epub_path,
            started_at=started_at,
            status=STATUS_UNAVAILABLE,
            version="unavailable",
            returncode=None,
            stderr="EPUBCheck is unavailable in the current environment.",
        )

    timeout = int(getattr(settings, "EPUBCHECK_TIMEOUT_SECONDS", 120))
    try:
        version_run = subprocess.run(
            [resolved, "--version"], capture_output=True, text=True, check=False, timeout=30
        )
        version = ((version_run.stdout or version_run.stderr).strip().splitlines() or ["unknown"])[0][:200]
        result = subprocess.run(
            [resolved, str(epub_path)], capture_output=True, text=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        return _report(
            path=epub_path,
            started_at=started_at,
            status=STATUS_FAILED,
            version=locals().get("version", "unknown"),
            returncode=None,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + f"\nEPUBCHECK_TIMEOUT after {timeout} seconds.",
        )
    except OSError as exc:
        return _report(
            path=epub_path,
            started_at=started_at,
            status=STATUS_UNAVAILABLE,
            version="unavailable",
            returncode=None,
            stderr=f"EPUBCheck is unavailable in the current environment: {exc}",
        )

    fatal_count, error_count, warning_count = _counts(f"{result.stdout}\n{result.stderr}")
    if result.returncode or fatal_count or error_count:
        status = STATUS_FAILED
    elif warning_count:
        status = STATUS_PASSED_WITH_WARNINGS
    else:
        status = STATUS_PASSED
    return _report(
        path=epub_path,
        started_at=started_at,
        status=status,
        version=version,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def write_report(path: Path, report: dict[str, object]) -> tuple[Path, str]:
    """Persist the complete, non-secret execution record beside the final build."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path, stream_sha256(path)
