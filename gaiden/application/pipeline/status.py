from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_FAILED = "failed"
STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"
STATUS_BLOCKED = "blocked"

VALID_STATUSES = {
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_FAILED,
    STATUS_COMPLETED,
    STATUS_SKIPPED,
    STATUS_BLOCKED,
}


@dataclass(frozen=True)
class ArtifactCheck:
    path: Path
    exists: bool
    size_bytes: int
    min_size_bytes: int
    valid: bool
    reason: str


def validate_artifact(
    path_value: str | Path,
    *,
    min_size_bytes: int = 1,
    required_suffix: str | None = None,
) -> ArtifactCheck:
    path = Path(path_value)
    if not path.exists():
        return ArtifactCheck(path, False, 0, min_size_bytes, False, "missing")
    if not path.is_file():
        return ArtifactCheck(path, False, 0, min_size_bytes, False, "not_a_file")
    if required_suffix and path.suffix.lower() != required_suffix.lower():
        size_bytes = path.stat().st_size
        return ArtifactCheck(path, True, size_bytes, min_size_bytes, False, "unexpected_suffix")
    size_bytes = path.stat().st_size
    if size_bytes < min_size_bytes:
        return ArtifactCheck(path, True, size_bytes, min_size_bytes, False, "too_small")
    return ArtifactCheck(path, True, size_bytes, min_size_bytes, True, "ok")


@dataclass(frozen=True)
class StageStatus:
    status: str
    reason: str
    evidence: tuple[ArtifactCheck, ...]


def resolve_stage_status(
    *,
    required_outputs: list[str | Path] | tuple[str | Path, ...] = (),
    min_size_bytes: int = 1,
    blocked_reason: str | None = None,
    failure_reason: str | None = None,
    running: bool = False,
    skipped_reason: str | None = None,
) -> StageStatus:
    if blocked_reason:
        return StageStatus(STATUS_BLOCKED, blocked_reason, ())
    if failure_reason:
        return StageStatus(STATUS_FAILED, failure_reason, ())
    if skipped_reason:
        return StageStatus(STATUS_SKIPPED, skipped_reason, ())

    evidence = tuple(
        validate_artifact(path_value, min_size_bytes=min_size_bytes)
        for path_value in required_outputs
    )
    if evidence and all(item.valid for item in evidence):
        return StageStatus(STATUS_COMPLETED, "all_outputs_valid", evidence)
    if running:
        return StageStatus(STATUS_RUNNING, "stage_marked_running", evidence)
    return StageStatus(STATUS_PENDING, "missing_or_invalid_outputs", evidence)


def resolve_block_status_map(
    *,
    raw_ready: bool,
    block_02_ready: bool,
    editorial_ready: bool,
    md_final_ready: bool,
    build_ready: bool,
    epub_ready: bool,
    pdf_ready: bool,
) -> dict[str, object]:
    block_02_running = bool(raw_ready and not block_02_ready)
    block_03_ready = block_02_ready
    block_03_done = bool(block_03_ready and editorial_ready)
    block_04_done = bool(build_ready and (epub_ready or pdf_ready))
    return {
        "bloco_01_ready": raw_ready,
        "bloco_02_running": block_02_running,
        "bloco_02_done": block_02_ready,
        "bloco_03_ready": block_03_ready,
        "bloco_03_unlocked": block_03_ready,
        "bloco_03_done": block_03_done,
        "bloco_04_done": block_04_done,
        "block_04_ready": bool(block_03_done and md_final_ready),
    }
