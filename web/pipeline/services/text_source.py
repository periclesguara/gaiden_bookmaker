from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pipeline.models import PipelineJob
from . import paths


@dataclass
class TextSourceInfo:
    canonical_name: str | None
    canonical_path: Path | None
    job_stage: str | None
    job_filepath: str | None
    job_id: int | None
    job_created_at: str | None


def get_effective_text_source(edition) -> TextSourceInfo:
    canonical_name = None
    canonical_path = None
    for name in paths.MERGE_PRIORITY:
        p = paths.edition_build_dir(edition) / name
        if p.exists():
            canonical_name = name
            canonical_path = p
            break

    job_stage = None
    job_filepath = None
    job_id = None
    job_created_at = None

    if canonical_name:
        if "polish" in canonical_name:
            stage = "polish"
        elif "refine" in canonical_name:
            stage = "refine"
        else:
            stage = "translate"

        job = (
            PipelineJob.objects.filter(
                book_code=edition.book_code,
                language=edition.language,
                stage=stage,
            )
            .order_by("-created_at")
            .first()
        )
        if job:
            job_stage = job.stage
            job_filepath = job.filepath
            job_id = job.id
            job_created_at = job.created_at.isoformat()

    return TextSourceInfo(
        canonical_name=canonical_name,
        canonical_path=canonical_path,
        job_stage=job_stage,
        job_filepath=job_filepath,
        job_id=job_id,
        job_created_at=job_created_at,
    )
