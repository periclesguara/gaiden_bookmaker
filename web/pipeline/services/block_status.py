from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gaiden.application.pipeline import official_body


@dataclass(frozen=True)
class BlockTwoCompletion:
    done: bool
    reason: str
    core_path: Path | None = None
    core_sha256: str = ""
    snapshot_id: int | None = None


def resolve_block_two_completion(edition, pipeline_state) -> BlockTwoCompletion:
    if (
        pipeline_state is None
        or getattr(pipeline_state, "edition_id", None) != edition.id
    ):
        return BlockTwoCompletion(False, "pipeline_edition_mismatch")

    core_path = official_body.resolve_official_body(edition)
    if core_path is None:
        return BlockTwoCompletion(False, "official_body_missing_or_invalid")

    snapshot = official_body.active_snapshot(edition)
    if snapshot is None:
        return BlockTwoCompletion(False, "active_snapshot_missing", core_path=core_path)
    return BlockTwoCompletion(
        True,
        f"official_body_valid:{snapshot.provenance}",
        core_path=core_path,
        core_sha256=snapshot.sha256,
        snapshot_id=snapshot.id,
    )
