from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from gaiden.manuscript_artifacts import write_once


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


def validate_epub(path: Path, *, executable: str = "epubcheck", timeout: int = 120) -> dict:
    path = path.resolve(strict=True)
    started = datetime.now(timezone.utc)
    report = {
        "schema": "gaiden_epubcheck_evidence_v1",
        "run_id": uuid.uuid4().hex,
        "tool": "EPUBCheck", "tool_version": "", "status": "BLOCKED",
        "artifact": str(path), "artifact_sha256": _digest(path),
        "started_at": started.isoformat(), "returncode": None,
        "stdout": "", "stderr": "", "messages": [],
        "fatal_count": None, "error_count": None, "warning_count": None,
        "reason": "Validation evidence is unavailable.",
    }
    with tempfile.TemporaryDirectory(prefix="gaiden-epubcheck-") as directory:
        json_path = Path(directory) / "report.json"
        try:
            version = subprocess.run(
                [executable, "--version"], capture_output=True, text=True,
                check=False, timeout=min(timeout, 30),
            )
            report["tool_version"] = _text(version.stdout or version.stderr).strip()
            result = subprocess.run(
                [executable, str(path), "--json", str(json_path)],
                capture_output=True, text=True, check=False, timeout=timeout,
            )
            report.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
            evidence = json.loads(json_path.read_text(encoding="utf-8"))
            messages = evidence.get("messages") if isinstance(evidence, dict) else None
            if not isinstance(messages, list) or any(
                not isinstance(message, dict) or message.get("severity") not in
                {"FATAL", "ERROR", "WARNING", "INFO", "USAGE", "SUPPRESSED"}
                for message in messages
            ):
                raise ValueError("EPUBCheck did not supply a recognized message list.")
            report["tool_report"] = evidence
            report["messages"] = messages
            for severity in ("FATAL", "ERROR", "WARNING"):
                report[f"{severity.lower()}_count"] = sum(
                    message["severity"] == severity for message in messages
                )
            severities = {message["severity"] for message in messages}
            if result.returncode or severities & {"ERROR", "FATAL"}:
                report.update(status="FAIL", reason="EPUBCheck rejected the artifact.")
            elif severities & {"WARNING", "USAGE", "SUPPRESSED"}:
                report.update(status="REVIEW_REQUIRED", reason="Validator warnings require editorial review.")
            elif report["tool_version"]:
                report.update(status="PASS", reason="EPUBCheck completed without errors or warnings.")
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            report.update(status="BLOCKED", reason=f"EPUBCheck evidence unavailable: {type(exc).__name__}")
            if isinstance(exc, subprocess.TimeoutExpired):
                report.update(stdout=_text(exc.stdout), stderr=_text(exc.stderr))
        try:
            report["observed_sha256"] = _digest(path)
        except OSError:
            report["observed_sha256"] = None
        if report["observed_sha256"] != report["artifact_sha256"]:
            report.update(status="FAIL", reason="Artifact changed during validation; validate the new bytes.")
    completed = datetime.now(timezone.utc)
    report["completed_at"] = completed.isoformat()
    report["duration_seconds"] = (completed - started).total_seconds()
    payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    archive = path.parent / ".epubcheck_reports" / f"{report['run_id']}.json"
    write_once(archive, payload)
    destination = path.with_name(path.name + ".epubcheck.json")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".epubcheck-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return report
