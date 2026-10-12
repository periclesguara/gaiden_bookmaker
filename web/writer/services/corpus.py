"""Canonical project scope over shared, prepared reference artifacts."""
import hashlib
import logging
import os
import re
from pathlib import Path

from django.db import transaction
from django.db.models import Q
from gaiden.infrastructure.storage import resolve_storage_path, storage_root
from gaiden.infrastructure.drive_storage import DriveStorageError, RcloneDriveStorage
from gaiden.writer_engine.index import VectorIndex
from editorial.models import EditionBuild, Work
from pipeline.models import IntakeItem

from ..models import ProjectReference, SourceDocument, SourceVectorIndex
from .manuscripts import ensure_project_writable
from .normalization import normalize_document, writer_storage_root

logger = logging.getLogger(__name__)

_TITLE_TOKEN_RE = re.compile(r"[a-z0-9]+")
_TITLE_ALIASES = {
    "sherlock holmes some later reminiscences": "sherlock holmes the memoirs of sherlock holmes",
}


def safe_path(value):
    if not value:
        return None
    roots = [storage_root(), writer_storage_root()]
    configured = os.environ.get("GAIDEN_WRITER_SOURCE_ROOT")
    if configured:
        roots.append(Path(configured).expanduser().resolve())
    raw = Path(value).expanduser()
    candidates = [raw] if raw.is_absolute() else [storage_root() / raw, resolve_storage_path(raw)]
    for candidate in candidates:
        path = candidate.resolve()
        if any(path.is_relative_to(root) for root in roots) and path.is_file():
            return path
    return None


def verified_body(index):
    if index.source_id:
        value, digest = index.source.official_body_path, index.source.official_body_sha256
    elif index.normalized_document_id:
        document = index.normalized_document
        value, digest = document.normalized_path, document.normalized_sha256
    else:
        return None
    path = safe_path(value)
    if not path or not digest:
        return None
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        return None
    return path, digest


def _title_key(value):
    tokens = _TITLE_TOKEN_RE.findall((value or "").casefold())
    if tokens and tokens[-1] in {"txt", "md", "html", "epub"}:
        tokens = tokens[:-1]
    return " ".join(tokens)


def _candidate_title_keys(work):
    keys = {_title_key(work.title), _title_key(f"{work.author.name} {work.title}")}
    alias = _TITLE_ALIASES.get(_title_key(work.title))
    if alias:
        keys.add(alias)
    return {key for key in keys if key}


def _existing_source_document(work):
    wanted = _candidate_title_keys(work)
    documents = SourceDocument.objects.filter(
        status__in=(SourceDocument.Status.NORMALIZED, SourceDocument.Status.VECTORIZED),
    ).exclude(normalized_path="").exclude(normalized_sha256="").order_by("pk")
    for document in documents:
        filename_key = _title_key(Path(document.filename).stem)
        if any(key in filename_key or filename_key in key for key in wanted):
            return document
    return None


def index_ready(index):
    body = verified_body(index)
    path = safe_path(index.index_path)
    if not (body and index.status == SourceVectorIndex.Status.READY and index.source_hash == body[1] and path):
        return False
    try:
        stored = VectorIndex.load(path)
        return bool(stored.rows and len(stored.rows) == index.chunk_count and stored.model == index.embedding_model)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def reference_index(reference):
    work_id = reference.intake_work_id or (reference.source.edition.work_id if reference.source_id else None)
    if not work_id:
        return None
    # Older build-owned indexes stay usable without rewriting or copying them.
    candidates = list(SourceVectorIndex.objects.filter(
        Q(intake_work_id=work_id) | Q(source__edition__work_id=work_id)
    ).select_related("source", "normalized_document").order_by("-updated_at", "pk"))
    for index in candidates:
        if index_ready(index):
            return index
    for index in candidates:
        if verified_body(index):
            return index
    if reference.source_id and reference.source.edition.work_id == work_id:
        fallback = SourceVectorIndex(source=reference.source)
        if verified_body(fallback):
            return fallback
    return candidates[0] if candidates else None


@transaction.atomic
def save_corpus_selection(project, works, removed=()):
    project = type(project).objects.select_for_update().get(pk=project.pk)
    ensure_project_writable(project)
    work_ids = {work.pk for work in works}
    removed_ids = {work.pk for work in removed} - work_ids
    project.references.filter(Q(intake_work_id__in=removed_ids) |
                              Q(intake_work__isnull=True, source__edition__work_id__in=removed_ids)).delete()
    for work in works:
        reference = project.references.filter(Q(intake_work=work) |
                                             Q(intake_work__isnull=True, source__edition__work=work)).first()
        if reference:
            reference.intake_work = work
            reference.enabled = True
            reference.save(update_fields=("intake_work", "enabled", "updated_at"))
        else:
            ProjectReference.objects.create(project=project, intake_work=work)


def _raw_path(work):
    paths = list(work.editions.exclude(raw_source_path="").order_by("pk").values_list("raw_source_path", flat=True))
    for value in paths:
        path = safe_path(value)
        if path:
            return path
    for item in IntakeItem.objects.filter(book_code=work.code).exclude(canonical_path="").order_by("pk"):
        path = _intake_item_path(item)
        if path:
            return path
    raise ValueError("Arquivo fonte local indisponível para preparação.")


def _intake_item_path(item):
    path = safe_path(item.canonical_path)
    if path:
        return path
    if not item.sha256:
        return None
    destination = storage_root() / item.canonical_path
    try:
        RcloneDriveStorage().download_imported_to(item.canonical_path, destination)
    except (DriveStorageError, ValueError, OSError):
        logger.exception("Writer reference canonical download failed", extra={"intake_item_id": item.pk})
        return None
    path = safe_path(str(destination))
    if path and hashlib.sha256(path.read_bytes()).hexdigest() == item.sha256:
        return path
    logger.error("Writer reference canonical download hash mismatch", extra={"intake_item_id": item.pk})
    return None


def prepare_reference(reference):
    existing = reference_index(reference)
    if existing and verified_body(existing):
        return existing, False
    work_id = reference.intake_work_id or reference.source.edition.work_id
    # Serialize preparation/index claims for the canonical work across projects.
    with transaction.atomic():
        work = Work.objects.select_for_update().get(pk=work_id)
        existing = reference_index(reference)
        if existing and verified_body(existing):
            return existing, False
        for build in EditionBuild.objects.filter(edition__work=work).exclude(official_body_path="").order_by("-build_version", "-pk"):
            body = safe_path(build.official_body_path)
            if body and build.official_body_sha256 and hashlib.sha256(body.read_bytes()).hexdigest() == build.official_body_sha256:
                index, _ = SourceVectorIndex.objects.get_or_create(source=build)
                return index, False
        document = _existing_source_document(work)
        if document:
            index, _ = SourceVectorIndex.objects.get_or_create(intake_work=work)
            index.normalized_document = document
            if index.source_hash != document.normalized_sha256:
                index.status = SourceVectorIndex.Status.NOT_INDEXED
                index.index_path = ""
                index.chunk_count = 0
            index.error_message = ""
            index.save(update_fields=(
                "normalized_document", "status", "index_path",
                "chunk_count", "error_message", "updated_at",
            ))
            return index, False
        raw = _raw_path(work)
        index, _ = SourceVectorIndex.objects.get_or_create(intake_work=work)
        document = index.normalized_document
        if document is None:
            document = SourceDocument.objects.filter(source_type=SourceDocument.SourceType.LOCAL_FILE, source_path=str(raw)).order_by("pk").first()
        if document is None:
            document = SourceDocument.objects.create(filename=raw.name, source_path=str(raw))
        elif document.source_path != str(raw):
            document.source_path = str(raw)
            document.save(update_fields=("source_path",))
        normalize_document(document)
        index.normalized_document = document
        index.status = SourceVectorIndex.Status.NOT_INDEXED
        index.error_message = ""
        index.save(update_fields=("normalized_document", "status", "error_message", "updated_at"))
        return index, True


def prepare_project_corpus(project):
    ensure_project_writable(project)
    result = {"prepared": 0, "reused": 0, "pending": 0}
    for reference in project.references.filter(enabled=True).select_related("source__edition", "intake_work"):
        try:
            _, created = prepare_reference(reference)
            result["prepared" if created else "reused"] += 1
        except (ValueError, OSError, KeyError):
            result["pending"] += 1
            logger.exception("Writer reference preparation failed", extra={"reference_id": reference.pk})
    return result
