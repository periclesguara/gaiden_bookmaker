from .embedding import configured_embedder
from .models import ProjectCorpus
from .services import CorpusCatalogService, retrieve_for_project


def is_canonical(project):
    config = getattr(project, "canonical_corpus", None)
    return bool(config and config.backend == "CANONICAL_CORPUS")


def source_rag(project, chapter, *, top_k=8, max_context_chars=24000):
    config = ProjectCorpus.objects.get(project=project, backend="CANONICAL_CORPUS")
    if not config.works.exists():
        return []
    query = "\n".join(str(getattr(chapter, field, "") or "") for field in ("title", "direction", "script"))
    if not query.strip():
        return []
    hits = retrieve_for_project(project, query[:24000], configured_embedder(), top_k=top_k)
    selected, used = [], 0
    for hit in hits:
        if used + len(hit["text"]) <= max_context_chars:
            selected.append(hit)
            used += len(hit["text"])
    return selected


def scope_status(project):
    config = ProjectCorpus.objects.get(project=project, backend="CANONICAL_CORPUS")
    service = CorpusCatalogService()
    groups, blockers = {}, []
    normalized = vectorized = chunks = 0
    models, dimensions = set(), set()
    works = list(config.works.select_related("author"))
    for work in works:
        row = service.status(work)
        group = groups.setdefault(work.author_id, {"name": work.author.name, "selected": 0, "vectorized": 0})
        group["selected"] += 1
        normalized += bool(row["normalized"])
        if row["rag_ready"]:
            vectorized += 1
            group["vectorized"] += 1
            chunks += row["chunk_count"]
            source = work.corpus_sources.get(canonical_for_rag=True)
            record = source.normalized_artifacts.order_by("-pk").first().vectorizations.order_by("-pk").first()
            models.add(record.embedding_model)
            dimensions.add(record.embedding_dimension)
        else:
            blockers.append({"title": work.title, "reason": "Corpus canonico ainda nao preparado."})
    total = len(works)
    ready = bool(total and vectorized == total)
    return {"selected": total, "normalized": normalized, "vectorized": vectorized, "failed": 0,
        "all_normalized": bool(total and normalized == total), "all_vectorized": ready,
        "authors": sorted(groups.values(), key=lambda r: r["name"]), "blockers": blockers,
        "preparation_pending": total - normalized, "vectorization_pending": total - vectorized,
        "vectorization_available": False,
        "index": {"recorded": ready, "exists": ready, "valid": ready, "path": "", "source_count": vectorized,
                  "chunk_count": chunks, "dimension": next(iter(dimensions)) if len(dimensions) == 1 else 0,
                  "embedding_model": ", ".join(sorted(models)), "created_at": "", "reason": "Pronto" if ready else "Pendente"}}
