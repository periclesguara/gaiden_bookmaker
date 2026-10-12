import json
import logging
import tempfile
from pathlib import Path

from django.apps import apps
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from editorial.models import Work
from gaiden.writer_engine.corpus import SourceChunk, chunk_source
from gaiden.writer_engine.index import VectorIndex
from .models import (BatchMember, CorpusBatch, CorpusChunk, CorpusSource,
                     NormalizedArtifact, ProjectCorpus, VectorizedWork)
from .storage import digest, root, verified_path, write_immutable
from .text import CHUNK_STRATEGY_VERSION, NORMALIZER_VERSION, body_only, section_spans

logger = logging.getLogger(__name__)
SHERLOCK_CODES = frozenset(("book_0001", "book_0002", "book_0006", "book_0008", "book_0009", "book10", "book_011", "book_017", "book_018"))


def fingerprint(artifact, model, dimension, model_digest):
    return digest(json.dumps([artifact.normalized_sha256, CHUNK_STRATEGY_VERSION,
                              model, dimension, model_digest], separators=(",", ":")).encode())


class CorpusSourceResolver:
    def available(self, work_id):
        work = Work.objects.get(pk=work_id)
        candidates = []
        try:
            IntakeItem = apps.get_model("pipeline", "IntakeItem")
        except LookupError:
            IntakeItem = None
        if IntakeItem:
            for item in IntakeItem.objects.filter(book_code=work.code).exclude(canonical_path="").order_by("pk"):
                try:
                    path = verified_path(item.canonical_path, item.sha256 or None)
                    candidates.append({"origin": "INTAKE", "path": str(path),
                                       "sha256": digest(path.read_bytes()), "canonical_for_rag": False})
                except ValueError:
                    pass
        for source in work.corpus_sources.order_by("-canonical_for_rag", "pk"):
            try:
                path = verified_path(source.raw_path, source.sha256)
                candidates.append({"origin": source.provider, "source_id": source.pk,
                                   "path": str(path), "sha256": source.sha256,
                                   "canonical_for_rag": source.canonical_for_rag})
            except ValueError:
                if source.canonical_for_rag:
                    raise
        editions = list(work.editions.select_related("texts").order_by("pk"))
        def add_path(value, origin):
            if not value:
                return
            choices = [value]
            # Recorded paths stay unchanged; expose only a verified storage mirror.
            if "/data/" in str(value):
                choices.append(root() / str(value).split("/data/", 1)[1])
            for candidate in choices:
                try:
                    path = verified_path(candidate)
                    candidates.append({"origin": origin, "path": str(path),
                                       "sha256": digest(path.read_bytes()), "canonical_for_rag": False})
                    return
                except ValueError:
                    pass
        for edition in editions:
            try:
                add_path(edition.texts.raw_path, "BOOKMAKER_LEGACY")
            except apps.get_model("editorial", "EditionText").DoesNotExist:
                pass
        for edition in editions:
            try:
                if edition.texts.raw_text:
                    candidates.append({"origin": "BOOKMAKER_LEGACY", "raw_text": edition.texts.raw_text,
                                       "sha256": digest(edition.texts.raw_text.encode()),
                                       "representation": "edition-text-utf8-v1", "canonical_for_rag": False})
            except apps.get_model("editorial", "EditionText").DoesNotExist:
                pass
        for edition in editions:
            add_path(edition.raw_source_path, "BOOKMAKER_LEGACY")
        folder = root() / "raw" / work.code
        if folder.is_dir():
            for path in sorted(folder.rglob("*")):
                if path.suffix.casefold() in {".txt", ".html", ".epub"}:
                    add_path(path, "BOOKMAKER_LEGACY")
        return candidates

    def resolve(self, work_id):
        candidates = self.available(work_id)
        canonical = [s for s in candidates if s.get("canonical_for_rag")]
        if canonical:
            return canonical[0]
        if candidates:
            return candidates[0]
        return {"status": "SOURCE_MISSING"}


def preview_manifest(manifest):
    entries = manifest["sources"]
    expected = set(manifest["work_codes"])
    conflicts, matched = [], []
    if manifest.get("batch_code") == "doyle_sherlock_complete" and expected != SHERLOCK_CODES:
        conflicts.append("Sherlock batch requires all nine approved Work identities")
    if len(expected) != len(entries) or {e["work_code"] for e in entries} != expected:
        conflicts.append("Batch source membership differs from approved Work codes")
    seen_provider_ids = set()
    for entry in entries:
        work = Work.objects.select_related("author").filter(code=entry["work_code"]).first()
        if not work or work.author_id != manifest["author_id"] or work.author.name != manifest["author_name"]:
            conflicts.append(f"Work identity requires review: {entry['work_code']}")
            continue
        approved = entry.get("approved_existing_titles", [entry["title"]])
        if work.title not in approved:
            conflicts.append(f"Unapproved catalog title: {work.code}")
        key = (entry["provider"], entry["provider_file_id"])
        if key in seen_provider_ids:
            conflicts.append("One provider document maps to multiple Works")
        seen_provider_ids.add(key)
        try:
            path = verified_path(entry["raw_path"], entry["sha256"])
            if not entry.get("provenance", {}).get("body_start_heading"):
                conflicts.append(f"Unreviewed narrative boundaries: {work.code}")
            if not path.stat().st_size:
                conflicts.append(f"Empty source: {work.code}")
        except ValueError:
            conflicts.append(f"Source missing or changed: {work.code}")
        existing = CorpusSource.objects.filter(work=work, canonical_for_rag=True).first()
        if existing and (existing.sha256 != entry["sha256"] or existing.representation != entry["representation"]):
            conflicts.append(f"Canonical source conflict: {work.code}")
        matched.append({"work_id": work.pk, "code": work.code, "action": "REUSE SOURCE" if existing else "REGISTER SOURCE"})
    return {"author": manifest["author_name"], "files_found": len(entries),
            "existing_work_matches": len(matched), "new_works": 0, "conflicts": conflicts,
            "manifest": "IGNORED AS NON-WORK", "matches": matched}


@transaction.atomic
def register_manifest(manifest):
    # Lock global identities before revalidating the entire batch.
    list(Work.objects.select_for_update().filter(code__in=manifest["work_codes"]).order_by("pk"))
    preview = preview_manifest(manifest)
    if preview["conflicts"]:
        raise ValueError("Batch requires review: " + "; ".join(preview["conflicts"]))
    batch, _ = CorpusBatch.objects.get_or_create(code=manifest["batch_code"], defaults={"author_id": manifest["author_id"]})
    batch = CorpusBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.author_id != manifest["author_id"]:
        raise ValueError("Batch author mismatch")
    existing_ids = set(batch.members.values_list("work__code", flat=True))
    if existing_ids and existing_ids != set(manifest["work_codes"]):
        raise ValueError("Existing batch membership cannot be replaced")
    for entry in manifest["sources"]:
        work = Work.objects.get(code=entry["work_code"])
        source, _ = CorpusSource.objects.get_or_create(
            work=work, representation=entry["representation"], sha256=entry["sha256"],
            defaults={"provider": entry["provider"], "provider_file_id": entry["provider_file_id"],
                      "title": entry["title"], "raw_path": entry["raw_path"],
                      "provenance": entry["provenance"], "canonical_for_rag": True})
        origins = list(source.provenance.get("origins", []))
        origin = {"provider": entry["provider"], "provider_file_id": entry["provider_file_id"],
                  "representation": entry["representation"], "sha256": entry["sha256"]}
        if origin not in origins:
            source.provenance = dict(source.provenance, origins=[*origins, origin])
            source.save(update_fields=("provenance",))
        if not source.canonical_for_rag:
            source.canonical_for_rag = True
            source.save(update_fields=("canonical_for_rag",))
        member, created = BatchMember.objects.get_or_create(batch=batch, work=work, defaults={"source": source})
        if not created and member.source_id != source.pk:
            raise ValueError("Existing batch source cannot be replaced")
        logger.info("corpus registration", extra={"work_id": work.pk, "source_id": source.pk,
                    "batch_id": batch.pk, "action": "REGISTER" if created else "REUSE", "hash": source.sha256,
                    "timestamp": timezone.now().isoformat()})
    return batch


def _members(batch):
    members = list(batch.members.select_related("work__author", "work__original_language", "source", "artifact").order_by("work_id"))
    if batch.code == "doyle_sherlock_complete" and {member.work.code for member in members} != SHERLOCK_CODES:
        raise ValueError("Sherlock batch requires all nine approved Work identities")
    if not members or any(m.work.author_id != batch.author_id or m.source.work_id != m.work_id
            or (m.artifact_id and (m.artifact.work_id != m.work_id or m.artifact.source_id != m.source_id))
            for m in members):
        raise ValueError("Invalid batch membership")
    return members


def _transition(batch, operation):
    try:
        with transaction.atomic():
            locked = CorpusBatch.objects.select_for_update().get(pk=batch.pk)
            old_status = locked.status
            operation(locked, _members(locked))
            locked.error = ""
            locked.save(update_fields=("status", "fingerprint", "error", "updated_at"))
        for member in locked.members.all():
            logger.info("corpus stage completed", extra={"work_id": member.work_id,
                "source_id": member.source_id, "batch_id": locked.pk, "action": locked.status,
                "old_status": old_status, "new_status": locked.status, "hash": locked.fingerprint,
                "timestamp": timezone.now().isoformat()})
        batch.refresh_from_db()
    except Exception as exc:
        CorpusBatch.objects.filter(pk=batch.pk).update(status="FAILED", error=type(exc).__name__)
        raise
    return batch


def normalize_batch(batch):
    def operation(batch, members):
        for member in members:
            source = member.source
            raw = verified_path(source.raw_path, source.sha256).read_text(encoding="utf-8")
            artifact = NormalizedArtifact.objects.filter(source=source, source_sha256=source.sha256,
                                                         normalizer_version=NORMALIZER_VERSION).first()
            if artifact:
                verified_path(artifact.normalized_path, artifact.normalized_sha256)
            else:
                text, report = body_only(raw, source.provenance)
                sha = digest(text.encode())
                path = write_immutable(f"normalized/{member.work_id}/{sha}.txt", text.encode())
                artifact = NormalizedArtifact.objects.create(source=source, work=member.work,
                    source_sha256=source.sha256, normalized_sha256=sha, normalizer_version=NORMALIZER_VERSION,
                    normalized_path=str(path), normalization_report=report)
            member.artifact = artifact
            member.save(update_fields=("artifact",))
        batch.status = "NORMALIZED"
    return _transition(batch, operation)


def chunk_batch(batch):
    def operation(batch, members):
        if any(not m.artifact_id for m in members):
            raise ValueError("Every Work must be normalized before chunking")
        for member in members:
            artifact = member.artifact
            path = verified_path(artifact.normalized_path, artifact.normalized_sha256)
            if artifact.chunks.filter(strategy_version=CHUNK_STRATEGY_VERSION).exists():
                continue
            rows = []
            spans = section_spans(path.read_text(encoding="utf-8"), artifact.normalization_report["section_titles"])
            with tempfile.TemporaryDirectory(prefix="corpus-chunk-") as temporary:
                temp_root = Path(temporary)
                for section_number, (heading, text) in enumerate(spans):
                    input_path = temp_root / f"section-{section_number}.txt"
                    input_path.write_text(text, encoding="utf-8")
                    for chunk in chunk_source(input_path, temp_root):
                        ordinal = len(rows)
                        identity = digest(f"{artifact.pk}:{CHUNK_STRATEGY_VERSION}:{ordinal}:{chunk.text_sha256}".encode())
                        rows.append(CorpusChunk(artifact=artifact, work=member.work, author=member.work.author,
                            source=member.source, story_or_section=heading, chunk_index=ordinal,
                            chunk_id=identity, text=chunk.text, text_sha256=chunk.text_sha256,
                            language=member.work.original_language.code, strategy_version=CHUNK_STRATEGY_VERSION))
            if not rows:
                raise ValueError("Work has no valid chunks")
            CorpusChunk.objects.bulk_create(rows)
        batch.status = "CHUNKED"
    return _transition(batch, operation)


def _stored_chunks(artifact):
    chunks = list(artifact.chunks.filter(strategy_version=CHUNK_STRATEGY_VERSION))
    if any(c.work_id != artifact.work_id or c.source_id != artifact.source_id
           or c.author_id != artifact.work.author_id or c.text_sha256 != digest(c.text.encode()) for c in chunks):
        raise ValueError("Chunk provenance differs from its normalized artifact")
    return [SourceChunk(chunk_id=c.chunk_id, source_path=f"work-{c.work_id}/{artifact.normalized_sha256}.txt",
        source_sha256=artifact.normalized_sha256, ordinal=c.chunk_index, heading=c.story_or_section,
        text=c.text, text_sha256=c.text_sha256) for c in chunks]


def load_vector(record):
    artifact = record.artifact
    verified_path(artifact.normalized_path, artifact.normalized_sha256)
    path = verified_path(record.index_path, record.index_sha256)
    if record.status != "VECTOR_READY" or record.embedding_model == "local-lexical-bm25":
        raise ValueError("Corpus is not dense vector ready")
    stored = VectorIndex.load(path)
    chunks = _stored_chunks(artifact)
    if (stored.model != record.embedding_model or stored.dimension != record.embedding_dimension
            or len(stored.rows) != record.embedding_count or record.embedding_count != record.chunk_count
            or [c for c, _ in stored.rows] != chunks
            or record.fingerprint != fingerprint(artifact, record.embedding_model, record.embedding_dimension, record.model_digest)):
        raise ValueError("Vector manifest is incompatible")
    return stored


def vectorize_batch(batch, embedder):
    if any(word in embedder.model.casefold() for word in ("bm25", "lexical")) or embedder.dimension < 1:
        raise ValueError("A dense embedding model with an explicit dimension is required")
    def operation(batch, members):
        if any(not m.artifact_id or not m.artifact.chunks.exists() for m in members):
            raise ValueError("ALL batch Works must be normalized and chunked")
        records = []
        for member in members:
            artifact = member.artifact
            verified_path(member.source.raw_path, member.source.sha256)
            verified_path(artifact.normalized_path, artifact.normalized_sha256)
            fp = fingerprint(artifact, embedder.model, embedder.dimension, embedder.model_digest)
            record = VectorizedWork.objects.filter(artifact=artifact, fingerprint=fp).first()
            if record:
                load_vector(record)
            else:
                chunks = _stored_chunks(artifact)
                destination = root() / "corpus" / f"vectors/{member.work_id}/{fp}.jsonl"
                if destination.exists():
                    cached = VectorIndex.load(verified_path(destination))
                    if (cached.model != embedder.model or cached.dimension != embedder.dimension
                            or [c for c, _ in cached.rows] != chunks):
                        raise ValueError("Existing vector artifact differs from approved chunks")
                    vectors = [v for _, v in cached.rows]
                    content = destination.read_bytes()
                else:
                    vectors = embedder.embed(["search_document: " + c.text for c in chunks])
                    if len(vectors) != len(chunks) or any(len(v) != embedder.dimension for v in vectors):
                        raise ValueError("Embedding count or dimension differs from chunks")
                    index = VectorIndex(model=embedder.model, dimension=embedder.dimension, rows=list(zip(chunks, vectors)))
                    with tempfile.TemporaryDirectory(prefix="corpus-vectors-") as temporary:
                        temporary_path = Path(temporary) / "index.jsonl"
                        index.save(temporary_path)
                        # Loading validates finite, nonzero and dimension-consistent vectors.
                        VectorIndex.load(temporary_path)
                        content = temporary_path.read_bytes()
                path = write_immutable(f"vectors/{member.work_id}/{fp}.jsonl", content)
                record = VectorizedWork.objects.create(artifact=artifact, fingerprint=fp,
                    embedding_model=embedder.model, model_digest=embedder.model_digest,
                    embedding_dimension=embedder.dimension, embedding_count=len(vectors), chunk_count=len(chunks),
                    chunk_strategy_version=CHUNK_STRATEGY_VERSION, index_path=str(path), index_sha256=digest(content))
            records.append((member.work_id, record.fingerprint))
        batch.fingerprint = digest(json.dumps(records, separators=(",", ":")).encode())
        batch.status = "VECTOR_READY"
    return _transition(batch, operation)


def batch_status(batch):
    try:
        members = _members(batch)
    except ValueError:
        return {"batch": batch.code, "works": batch.members.count(), "sources": 0,
                "normalized": 0, "chunked": 0, "vectorized": 0, "chunk_count": 0,
                "embedding_count": 0, "errors": ["INVALID_MEMBERSHIP"],
                "rag_status": "BLOCKED", "batch_fingerprint": batch.fingerprint}
    sources = normalized = chunked = vectorized = chunks = embeddings = 0
    records = []
    errors = []
    for member in members:
        try:
            verified_path(member.source.raw_path, member.source.sha256)
            sources += 1
            if member.artifact_id:
                if member.artifact.source_sha256 != member.source.sha256:
                    raise ValueError("Normalized source identity mismatch")
                verified_path(member.artifact.normalized_path, member.artifact.normalized_sha256)
                normalized += 1
                count = member.artifact.chunks.filter(strategy_version=CHUNK_STRATEGY_VERSION).count()
                chunks += count
                chunked += bool(count)
                record = member.artifact.vectorizations.order_by("-pk").first()
                if record:
                    load_vector(record)
                    vectorized += 1
                    embeddings += record.embedding_count
                    records.append((member.work_id, record.fingerprint))
        except (ValueError, OSError, KeyError, TypeError):
            errors.append(member.work_id)
    expected = digest(json.dumps(records, separators=(",", ":")).encode())
    ready = (not errors and len(members) == normalized == chunked == vectorized
             and batch.status == "VECTOR_READY" and expected == batch.fingerprint)
    return {"batch": batch.code, "works": len(members), "sources": sources,
            "normalized": normalized, "chunked": chunked, "vectorized": vectorized,
            "chunk_count": chunks, "embedding_count": embeddings, "errors": errors,
            "rag_status": "READY" if ready else "BLOCKED", "batch_fingerprint": batch.fingerprint}


class CorpusCatalogService:
    def __init__(self):
        self.batch_states = {}

    def works(self):
        return Work.objects.select_related("author", "original_language").order_by("author__name", "title", "pk")

    def status(self, work):
        source = work.corpus_sources.filter(canonical_for_rag=True).first()
        artifact = source.normalized_artifacts.order_by("-pk").first() if source else None
        record = artifact.vectorizations.order_by("-pk").first() if artifact else None
        ready = False
        if record:
            try:
                load_vector(record)
                batches = CorpusBatch.objects.filter(members__artifact=artifact, status="VECTOR_READY")
                for batch in batches:
                    if batch.pk not in self.batch_states:
                        self.batch_states[batch.pk] = batch_status(batch)
                    if self.batch_states[batch.pk]["rag_status"] == "READY":
                        ready = True
            except (ValueError, OSError, KeyError, TypeError):
                pass
        return {"work": work, "display_title": source.title if source else work.title,
                "source": bool(source), "normalized": bool(artifact),
                "chunk_count": artifact.chunks.count() if artifact else 0,
                "vector_status": "VECTOR_READY" if ready else "NOT_PREPARED", "rag_ready": ready}


@transaction.atomic
def save_selection(project, work_ids, scope=None):
    config = ProjectCorpus.objects.select_for_update().get(project=project)
    current_project = apps.get_model("writer", "StoryProject").objects.select_for_update().get(pk=project.pk)
    if getattr(current_project, "status", None) == "CLOSED":
        raise ValueError("Closed project corpus is read-only")
    if config.backend != "CANONICAL_CORPUS":
        raise ValueError("Legacy project backend is immutable in this workflow")
    ids = set(work_ids)
    if scope is not None:
        scope = set(scope)
        if not ids.issubset(scope):
            raise ValueError("Selection is outside displayed scope")
        ids |= set(config.works.values_list("pk", flat=True)) - scope
    works = list(Work.objects.filter(pk__in=ids))
    if len(works) != len(ids):
        raise ValueError("Invalid Work selection")
    config.works.set(works)


def selected_index(project, embedder):
    config = ProjectCorpus.objects.get(project=project, backend="CANONICAL_CORPUS")
    rows = []
    service = CorpusCatalogService()
    for work in config.works.order_by("pk"):
        if not service.status(work)["rag_ready"]:
            raise ValueError("Selected Work belongs to a non-ready batch")
        source = work.corpus_sources.get(canonical_for_rag=True)
        artifact = source.normalized_artifacts.order_by("-pk").first()
        fp = fingerprint(artifact, embedder.model, embedder.dimension, embedder.model_digest)
        stored = load_vector(VectorizedWork.objects.get(artifact=artifact, fingerprint=fp))
        rows.extend(stored.rows)
    if not rows:
        raise ValueError("Select prepared Works before retrieval")
    return VectorIndex(model=embedder.model, dimension=embedder.dimension, rows=rows)


def retrieve_for_project(project, query, embedder, top_k=8):
    index = selected_index(project, embedder)
    hits = index.search("search_query: " + query, embedder, top_k=top_k)
    ids = [hit.chunk.chunk_id for hit in hits]
    metadata = {c.chunk_id: c for c in CorpusChunk.objects.filter(chunk_id__in=ids, work__projectcorpus__project=project)}
    return [{"work_id": metadata[h.chunk.chunk_id].work_id, "author_id": metadata[h.chunk.chunk_id].author_id,
             "source_id": metadata[h.chunk.chunk_id].source_id, "normalized_artifact_id": metadata[h.chunk.chunk_id].artifact_id,
             "source_work_id": metadata[h.chunk.chunk_id].work_id, "canonical_work_id": metadata[h.chunk.chunk_id].work_id,
             "title": metadata[h.chunk.chunk_id].work.title, "author": metadata[h.chunk.chunk_id].author.name,
             "source_build_id": None, "source_version": 1, "reference_roles": [], "reference_notes": "",
             "retrieval_method": "local_dense_vector", "heading": h.chunk.heading,
             "source_path": h.chunk.source_path, "ordinal": h.chunk.ordinal,
             "chunk_id": h.chunk.chunk_id, "story_or_section": h.chunk.heading,
             "score": h.score, "text": h.chunk.text} for h in hits]
