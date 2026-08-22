from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from django.utils import timezone

from writer_engine.clients import CompatibleEmbeddingClient
from writer_engine.index import VectorIndex
from writer.models import SourceDocument, StoryProject
from writer.services.normalization import writer_storage_root


def embedding_client() -> CompatibleEmbeddingClient:
    return CompatibleEmbeddingClient(
        base_url=os.environ.get("WRITER_EMBEDDING_BASE_URL", "http://127.0.0.1:8001/v1"),
        api_key=os.environ.get("WRITER_EMBEDDING_API_KEY", "placeholder"),
        model=os.environ.get("WRITER_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"),
    )


def vectorize_project(project: StoryProject) -> Path:
    documents = list(project.sources.order_by("id"))
    if not documents:
        raise ValueError("select at least one normalized source")
    if any(document.status not in {
        SourceDocument.Status.NORMALIZED, SourceDocument.Status.VECTORIZED
    } for document in documents):
        raise ValueError("every selected source must be normalized before vectorization")
    identity = hashlib.sha256(
        "\n".join(f"{document.id}:{document.normalized_sha256}" for document in documents).encode()
    ).hexdigest()[:20]
    destination = writer_storage_root() / "indexes" / f"project-{project.id}-{identity}.jsonl"
    with tempfile.TemporaryDirectory(prefix="writer-index-") as temporary:
        staging = Path(temporary)
        for document in documents:
            source = Path(document.normalized_path).resolve(strict=True)
            shutil.copyfile(source, staging / f"{document.id:06d}-{source.name}")
        index = VectorIndex.build(staging, embedding_client())
        index.save(destination)
    now = timezone.now()
    project.vector_index_path = str(destination)
    project.save(update_fields=("vector_index_path", "updated_at"))
    project.sources.update(status=SourceDocument.Status.VECTORIZED, vectorized_at=now, error_message="")
    return destination
