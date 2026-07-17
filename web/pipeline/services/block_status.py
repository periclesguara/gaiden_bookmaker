from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from gaiden.application.pipeline import drive_return
from gaiden.infrastructure import storage
from pipeline.models import TextSnapshot


@dataclass(frozen=True)
class BlockTwoCompletion:
    done: bool
    reason: str
    core_path: Path | None = None
    core_sha256: str = ""
    snapshot_id: int | None = None


def _core_path(pipeline_state) -> Path | None:
    configured = (getattr(pipeline_state, "core_last_txt_path", "") or "").strip()
    if not configured:
        return None
    candidate = Path(configured)
    if not candidate.is_absolute():
        candidate = storage.repo_root() / candidate
    if candidate.is_symlink() or not candidate.is_file():
        return None
    return candidate


def resolve_block_two_completion(edition, pipeline_state) -> BlockTwoCompletion:
    if (
        pipeline_state is None
        or getattr(pipeline_state, "edition_id", None) != edition.id
    ):
        return BlockTwoCompletion(False, "pipeline_edition_mismatch")

    core_path = _core_path(pipeline_state)
    if core_path is None:
        return BlockTwoCompletion(False, "core_missing")

    if (
        drive_return.pending_path(edition).exists()
        or drive_return.pending_metadata_path(edition).exists()
    ):
        return BlockTwoCompletion(False, "return_pending", core_path=core_path)

    active = list(
        TextSnapshot.objects.filter(
            edition_id=edition.id,
            stage="drive_return_reference",
        ).order_by("-created_at", "-id")[:2]
    )
    if len(active) != 1:
        return BlockTwoCompletion(False, "active_snapshot_missing_or_ambiguous", core_path=core_path)

    snapshot = active[0]
    core_sha256 = hashlib.sha256(core_path.read_bytes()).hexdigest()
    snapshot_sha256 = hashlib.sha256(snapshot.content.encode("utf-8")).hexdigest()
    if core_sha256 != snapshot_sha256:
        return BlockTwoCompletion(
            False,
            "snapshot_sha_mismatch",
            core_path=core_path,
            core_sha256=core_sha256,
            snapshot_id=snapshot.id,
        )
    return BlockTwoCompletion(
        True,
        "active_drive_return_reference_matches_core",
        core_path=core_path,
        core_sha256=core_sha256,
        snapshot_id=snapshot.id,
    )
