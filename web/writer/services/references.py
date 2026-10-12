from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from django.db import transaction
from editorial.models import EditionBuild, Work
from gaiden.writer_engine.index import VectorIndex
from gaiden.writer_engine.corpus import load_corpus
from .normalization import writer_storage_root
from .corpus import index_ready, reference_index, verified_body
from .manuscripts import ensure_project_writable
from ..models import ProjectReference, SourceVectorIndex

logger = logging.getLogger(__name__)


@transaction.atomic
def set_project_references(project, sources):
    selected = {
        source.edition.work_id: source.pk
        for source in sources
        if source.qualifies_as_done
    }
    for work_id, source_id in selected.items():
        existing = project.references.filter(intake_work_id=work_id).first()
        if existing is None:
            existing = project.references.filter(source_id=source_id).first()
        if existing:
            existing.intake_work_id = work_id
            existing.source_id = source_id
            existing.save(update_fields=("intake_work", "source", "updated_at"))
        else:
            ProjectReference.objects.create(project=project, intake_work_id=work_id, source_id=source_id)
    logger.info("writer project references updated", extra={"project_id": project.pk, "source_count": len(selected)})


def index_status(source):
    index, _ = SourceVectorIndex.objects.get_or_create(source=source)
    if index.status == SourceVectorIndex.Status.READY and index.source_hash != source.official_body_sha256:
        index.status = SourceVectorIndex.Status.OUTDATED
        index.save(update_fields=("status", "updated_at"))
        logger.info("writer source vector index invalidated", extra={"source_id": source.pk})
    return index


def ready_index(source):
    existing = SourceVectorIndex.objects.filter(source=source).first()
    if existing and index_ready(existing):
        return existing
    return None


def latest_reference_source(work_id):
    return EditionBuild.objects.filter(
        edition__work_id=work_id,
        status=EditionBuild.STATUS_DONE,
        is_final=True,
        validation_passed=True,
    ).order_by("-build_version", "-created_at", "-pk").first()


def vectorize_source(source, *, force=False):
    existing = ready_index(source)
    if existing is not None and not force:
        return existing
    if not source.qualifies_as_done:
        raise ValueError("A fonte do Intake não está DONE ou perdeu integridade.")
    index = index_status(source)
    body = source._safe_existing_path(source.official_body_path)
    if body is None:
        raise ValueError("Corpo normalizado canônico indisponível.")
    return _build_reference_index(index, body, source.official_body_sha256)


def _build_reference_index(index, body, digest):
    index.status = SourceVectorIndex.Status.PROCESSING
    index.error_message = ""
    index.save(update_fields=("status", "error_message", "updated_at"))
    logger.info("writer source vectorization started", extra={"index_id": index.pk})
    try:
        identity = f"source-{index.source_id}" if index.source_id else f"work-{index.intake_work_id}"
        destination = writer_storage_root() / "reference_indexes" / f"{identity}-{digest[:16]}.jsonl"
        with tempfile.TemporaryDirectory(prefix="gaiden-reference-index-") as temporary:
            # The corpus loader rejects symlinks; staging is temporary only.
            shutil.copyfile(body, Path(temporary) / "reference.txt")
            _, chunks = load_corpus(Path(temporary))
            built = VectorIndex(
                model="local-lexical-bm25",
                dimension=1,
                rows=[(chunk, [1.0]) for chunk in chunks],
            )
            built.save(destination)
        index.source_hash = digest
        index.embedding_model = built.model
        index.chunk_count = len(built.rows)
        index.index_path = str(destination)
        index.status = SourceVectorIndex.Status.READY
        index.error_message = ""
        index.save()
        logger.info("writer source vectorization completed", extra={"index_id": index.pk, "chunk_count": index.chunk_count})
    except Exception as exc:
        index.status = SourceVectorIndex.Status.FAILED
        index.error_message = str(exc)[:2000]
        index.save(update_fields=("status", "error_message", "updated_at"))
        logger.exception("writer source vectorization failed", extra={"index_id": index.pk})
        raise
    return index


def vectorize_project_references(project, *, force=False):
    ensure_project_writable(project)
    references = list(project.references.filter(enabled=True).select_related(
        "source", "intake_work",
    ).order_by("intake_work_id", "source_id", "pk"))
    if not references:
        raise ValueError("Selecione ao menos uma referência.")

    reused = 0
    vectorized = 0
    pending = []

    with transaction.atomic():
        for reference in references:
            if reference.source_id is None and reference.intake_work_id:
                source = latest_reference_source(reference.intake_work_id)
                if source is not None:
                    reference.source = source
                    reference.save(update_fields=("source", "updated_at"))

    for reference in references:
        if reference.source_id and reference.intake_work_id and reference.source.edition.work_id != reference.intake_work_id:
            pending.append(f"{reference.intake_work.title}: build vinculado pertence a outra obra")
            continue
        index = reference_index(reference)
        body = verified_body(index) if index else None
        if not body:
            pending.append(f"Referência {reference.pk}: preparação pendente")
            continue
        try:
            with transaction.atomic():
                work_id = reference.intake_work_id or reference.source.edition.work_id
                Work.objects.select_for_update().get(pk=work_id)
                index = reference_index(reference)
                body = verified_body(index) if index else None
                if not body:
                    pending.append(f"Referência {reference.pk}: preparação pendente")
                    continue
                if index.pk is None:
                    index, _ = SourceVectorIndex.objects.get_or_create(source=index.source)
                current = SourceVectorIndex.objects.select_for_update().get(pk=index.pk)
                if index_ready(current) and not force:
                    reused += 1
                    continue
                _build_reference_index(current, *body)
                vectorized += 1
        except Exception:
            # Persist failure after the claim transaction rolls back.
            SourceVectorIndex.objects.filter(pk=index.pk).update(
                status=SourceVectorIndex.Status.FAILED, error_message="Falha no serviço local de vetorização."
            )
            pending.append(f"Referência {reference.pk}: vetorização falhou")

    return {
        "selected": len(references),
        "reused": reused,
        "vectorized": vectorized,
        "pending": pending,
    }
