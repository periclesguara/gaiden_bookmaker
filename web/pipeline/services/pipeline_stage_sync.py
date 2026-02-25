from __future__ import annotations

from editorial.models import Edition, EditionPipeline


LEGACY_STAGE_MAP = {
    "RAW": Edition.STATUS_REGISTERED,
}


def normalized_stage_from_status(status: str | None) -> str:
    raw = (status or "").strip().upper()
    if not raw:
        return Edition.STATUS_REGISTERED
    mapped = LEGACY_STAGE_MAP.get(raw, raw)
    valid = {choice for choice, _ in Edition.STATUS_CHOICES}
    if mapped not in valid:
        return Edition.STATUS_REGISTERED
    return mapped


def sync_pipeline_stage(edition: Edition, *, created: bool = False) -> EditionPipeline:
    stage = normalized_stage_from_status(edition.status)
    pipeline, pipeline_created = EditionPipeline.objects.get_or_create(
        edition=edition,
        defaults={"current_stage": stage},
    )

    update_fields: list[str] = []
    if pipeline.current_stage != stage:
        pipeline.current_stage = stage
        update_fields.append("current_stage")

    # Keep created kwarg in signature for call sites/signals; no special behavior needed yet.
    _ = created
    _ = pipeline_created

    if update_fields:
        pipeline.save(update_fields=update_fields)
    return pipeline
