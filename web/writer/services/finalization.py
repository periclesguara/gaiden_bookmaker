"""Coordinate finalization using existing revisions; no editorial engine here."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from ..models import ManuscriptRevision, StoryProject
from .manuscript_pipeline import save_merged_manuscript
from .manuscripts import (_immutable_write, close_writer_project, ensure_project_writable,
                         promote_canonical_manuscript, revision_bytes)
from .normalization import writer_storage_root


FALLBACK_WARNING = "No imported Final Version. The latest Merge will be used."


def latest_merge(project):
    return project.manuscript_revisions.filter(
        source_kind=ManuscriptRevision.SourceKind.CHAPTER_MERGE
    ).order_by("-version").first()


def saved_final(project):
    revision = project.canonical_manuscript
    if revision and revision.source_kind == ManuscriptRevision.SourceKind.IMPORTED_FINAL:
        return revision
    return None


def edition_source(project):
    """Selection never builds chapters or substitutes a damaged saved final."""
    if project.status == StoryProject.Status.CLOSED:
        return project.canonical_manuscript
    return saved_final(project) or latest_merge(project)


@dataclass(frozen=True)
class MergeExport:
    path: Path
    data: bytes
    revision: ManuscriptRevision


@transaction.atomic
def export_merge(project) -> MergeExport:
    project = StoryProject.objects.select_for_update().get(pk=project.pk)
    revision = latest_merge(project)
    if revision is None:
        raise ValueError("salve um merge antes de exportar")
    data = revision_bytes(project, revision)
    directory = writer_storage_root() / "projects" / str(project.pk) / "return"
    directory.mkdir(parents=True, exist_ok=True)
    prefix = f"book_{project.pk}_merged_v{revision.version:03d}_export_v"
    versions = []
    for existing in directory.glob(prefix + "*"):
        token = existing.name[len(prefix):].split(".", 1)[0]
        if token.isdigit():
            versions.append(int(token))
    version = max(versions, default=0) + 1
    destination = directory / f"{prefix}{version:03d}.txt"
    _immutable_write(destination, data)
    try:
        _immutable_write(destination.with_suffix(".json"), (json.dumps({
            "schema": "writer_merge_export_v1", "project_id": project.pk,
            "revision_id": revision.pk, "merge_version": revision.version,
            "export_version": version, "sha256": revision.sha256,
            "exported_at": timezone.now().isoformat(),
        }, indent=2) + "\n").encode())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return MergeExport(destination, data, revision)


@transaction.atomic
def save_final(project, revision):
    project = StoryProject.objects.select_for_update().get(pk=project.pk)
    ensure_project_writable(project)
    if revision.source_kind != ManuscriptRevision.SourceKind.IMPORTED_FINAL:
        raise ValueError("selecione uma versão final importada")
    return promote_canonical_manuscript(project, revision)


@transaction.atomic
def prepare_edition_source(project, actor=None):
    """Freeze Final > latest Merge > final chapters for the existing Intake bridge."""
    project = StoryProject.objects.select_for_update().get(pk=project.pk)
    revision = edition_source(project)
    if project.status == StoryProject.Status.CLOSED:
        if revision is None:
            raise ValueError("projeto fechado sem manuscrito canônico")
        revision_bytes(project, revision)
        return project, revision.source_kind == ManuscriptRevision.SourceKind.CHAPTER_MERGE
    if revision is None:
        revision = save_merged_manuscript(project, actor)
    revision_bytes(project, revision)
    promote_canonical_manuscript(project, revision)
    project = close_writer_project(project, actor)
    return project, revision.source_kind == ManuscriptRevision.SourceKind.CHAPTER_MERGE
