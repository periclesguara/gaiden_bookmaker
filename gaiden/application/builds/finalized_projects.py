from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from editorial.models import EditionBuild, EditionBuildAuditEvent, EditionPipeline, PipelineStage


@dataclass(frozen=True)
class FinalizedProjectSyncResult:
    outcome: str
    build: EditionBuild | None
    reasons: tuple[str, ...] = ()


def latest_builds_by_edition(*, book_code: str | None = None) -> list[EditionBuild]:
    queryset = EditionBuild.objects.select_related(
        "edition__work__author", "edition__language", "edition__seal"
    )
    if book_code:
        queryset = queryset.filter(edition__work__code=book_code)
    latest: dict[int, EditionBuild] = {}
    for build in queryset.order_by("edition_id", "-build_version", "-created_at", "-id"):
        latest.setdefault(build.edition_id, build)
    return list(latest.values())


def finalized_project_builds(*, book_code: str | None = None) -> list[EditionBuild]:
    return sorted(
        (build for build in latest_builds_by_edition(book_code=book_code) if build.qualifies_as_done),
        key=lambda build: (build.edition.work.code.casefold(), build.locale.casefold()),
    )


def finalization_reasons(build: EditionBuild | None) -> tuple[str, ...]:
    if build is None:
        return ("no build is registered",)
    reasons: list[str] = []
    checks = (
        (build.status == EditionBuild.STATUS_DONE, "status is not DONE"),
        (build.is_final, "build is not marked final"),
        (build.validation_passed, "structural validation has not passed"),
        (bool(build.artifact_sha256), "artifact SHA-256 is missing"),
        (bool(build.artifact_size_bytes), "artifact size is missing"),
        (bool(build.artifact_source), "artifact source is missing"),
        (bool(build.official_body_path), "official body path is missing"),
        (bool(build.official_body_sha256), "official body SHA-256 is missing"),
        (bool(build.validated_at), "validation timestamp is missing"),
        (bool(build.approved_at), "approval timestamp is missing"),
        (bool(build.completed_at), "completion timestamp is missing"),
    )
    reasons.extend(message for passed, message in checks if not passed)
    if not reasons:
        reasons.extend(build.integrity_errors())
    return tuple(reasons)


@transaction.atomic
def sync_finalized_project(edition_id: int, *, actor: str = "system") -> FinalizedProjectSyncResult:
    latest = (
        EditionBuild.objects.select_for_update()
        .select_related("edition__work")
        .filter(edition_id=edition_id)
        .order_by("-build_version", "-created_at", "-id")
        .first()
    )
    reasons = finalization_reasons(latest)
    if reasons:
        return FinalizedProjectSyncResult("NOT_FINALIZED", latest, reasons)
    event, created = EditionBuildAuditEvent.objects.get_or_create(
        build=latest,
        event_type="FINALIZED_PROJECT_SYNCED",
        defaults={
            "actor": actor,
            "details": {
                "edition_id": edition_id,
                "book_code": latest.edition.work.code,
                "locale": latest.locale,
                "build_version": latest.build_version,
                "artifact_sha256": latest.artifact_sha256,
            },
        },
    )
    return FinalizedProjectSyncResult("PROJECTED" if created else "NO_OP", latest)


def _fallback_pipeline_stage(state: EditionPipeline) -> str:
    for field, stage in (
        ("final_md_at", PipelineStage.FINAL_MD),
        ("miolo_md_at", PipelineStage.MIOLO_MD),
        ("polished_at", PipelineStage.POLISHED),
        ("merged_at", PipelineStage.MERGED),
        ("refined_at", PipelineStage.REFINED),
        ("translated_at", PipelineStage.TRANSLATED),
        ("chunked_at", PipelineStage.CHUNKED),
        ("split_at", PipelineStage.SPLIT),
        ("normalized_at", PipelineStage.NORMALIZED),
    ):
        if getattr(state, field):
            return stage
    return PipelineStage.RAW


@transaction.atomic
def mark_build_outdated(build_id: int, *, actor: str, reason: str) -> EditionBuild:
    if not reason.strip():
        raise ValueError("A reason is required to mark a final build OUTDATED.")
    build = EditionBuild.objects.select_for_update().select_related("edition").get(pk=build_id)
    previous = {"status": build.status, "is_final": build.is_final}
    if build.status == EditionBuild.STATUS_OUTDATED and not build.is_final:
        return build
    build.status = EditionBuild.STATUS_OUTDATED
    build.is_final = False
    build.save(update_fields=["status", "is_final"])
    state, _ = EditionPipeline.objects.select_for_update().get_or_create(edition=build.edition)
    state.current_stage = _fallback_pipeline_stage(state)
    state.build_outdated = True
    state.editorial_changed = True
    state.save(update_fields=["current_stage", "build_outdated", "editorial_changed"])
    EditionBuildAuditEvent.objects.create(
        build=build,
        event_type="FINAL_ARTIFACT_MARKED_OUTDATED",
        actor=actor,
        details={
            "reason": reason.strip(),
            "previous": previous,
            "new": {"status": build.status, "is_final": build.is_final},
            "artifact_sha256": build.artifact_sha256,
            "official_body_sha256": build.official_body_sha256,
        },
    )
    return build
