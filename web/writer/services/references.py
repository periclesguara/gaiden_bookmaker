from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from gaiden.writer_engine.index import VectorIndex
from .vectorization import embedding_client
from .normalization import writer_storage_root
from ..models import ProjectReference, SourceVectorIndex

logger = logging.getLogger(__name__)


@transaction.atomic
def set_project_references(project, sources):
    source_ids = {source.pk for source in sources if source.qualifies_as_done}
    project.references.exclude(source_id__in=source_ids).delete()
    for source_id in source_ids:
        ProjectReference.objects.get_or_create(project=project, source_id=source_id)
    logger.info("writer project references updated", extra={"project_id": project.pk, "source_count": len(source_ids)})


def index_status(source):
    index, _ = SourceVectorIndex.objects.get_or_create(source=source)
    if index.status == SourceVectorIndex.Status.READY and index.source_hash != source.official_body_sha256:
        index.status = SourceVectorIndex.Status.OUTDATED
        index.save(update_fields=("status", "updated_at"))
        logger.info("writer source vector index invalidated", extra={"source_id": source.pk})
    return index


def vectorize_source(source):
    if not source.qualifies_as_done:
        raise ValueError("A fonte do Intake não está DONE ou perdeu integridade.")
    index = index_status(source)
    index.status = SourceVectorIndex.Status.PROCESSING
    index.error_message = ""
    index.save(update_fields=("status", "error_message", "updated_at"))
    logger.info("writer source vectorization started", extra={"source_id": source.pk})
    try:
        body = source._safe_existing_path(source.official_body_path)
        if body is None:
            raise ValueError("Corpo normalizado canônico indisponível.")
        destination = writer_storage_root() / "reference_indexes" / f"source-{source.pk}-{source.official_body_sha256[:16]}.jsonl"
        with tempfile.TemporaryDirectory(prefix="gaiden-reference-index-") as temporary:
            link = Path(temporary) / body.name
            os.symlink(body, link)
            built = VectorIndex.build(Path(temporary), embedding_client())
            built.save(destination)
        index.source_hash = source.official_body_sha256
        index.embedding_model = built.model
        index.chunk_count = len(built.rows)
        index.index_path = str(destination)
        index.status = SourceVectorIndex.Status.READY
        index.error_message = ""
        index.save()
        logger.info("writer source vectorization completed", extra={"source_id": source.pk, "chunk_count": index.chunk_count})
    except Exception as exc:
        index.status = SourceVectorIndex.Status.FAILED
        index.error_message = str(exc)[:2000]
        index.save(update_fields=("status", "error_message", "updated_at"))
        logger.exception("writer source vectorization failed", extra={"source_id": source.pk})
        raise
    return index
