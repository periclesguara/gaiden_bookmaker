from __future__ import annotations

import hashlib
from pathlib import Path

from django.utils import timezone
from django.db import connection

from gaiden.domain.editorial.collections import (
    COLLECTION_STATUS_FAILED,
    COLLECTION_STATUS_ITEMS_REGISTERED,
    COLLECTION_STATUS_MERGED,
    COLLECTION_STATUS_PREPARED,
    COLLECTION_STATUS_NORMALIZED,
    COLLECTION_STATUS_PIPELINE_RUNNING,
    COLLECTION_STATUS_READY_FOR_PIPELINE,
    COLLECTION_STATUS_UPLOADS_RECEIVED,
    ITEM_STATUS_COMPLETED,
    ITEM_STATUS_FAILED,
    ITEM_STATUS_PENDING,
    ITEM_STATUS_RUNNING,
    can_handoff_to_pipeline,
    validate_contiguous_order,
    validate_item_count,
    validate_no_duplicates,
)
from gaiden.infrastructure import collections_storage
from gaiden.infrastructure.collections_runner import (
    merge_collection_items,
    normalize_collection_items,
    prepare_collection_items,
    write_manifest,
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_collection_items(collection, items) -> list:
    active_items = [item for item in items if item.is_active]
    validate_item_count(len(active_items))
    validate_contiguous_order([item.order_index for item in active_items])
    validate_no_duplicates([(item.author_name.strip().lower(), item.work_title.strip().lower()) for item in active_items])
    return sorted(active_items, key=lambda obj: obj.order_index)


def items_registered(collection, *, item_count: int) -> None:
    collection.item_count = item_count
    collection.status = COLLECTION_STATUS_ITEMS_REGISTERED
    collection.save(update_fields=["item_count", "status", "updated_at"])


def collection_uploads_received(collection, *, item_count: int) -> None:
    collection.item_count = item_count
    collection.status = COLLECTION_STATUS_UPLOADS_RECEIVED
    collection.save(update_fields=["item_count", "status", "updated_at"])


def _require_ready_contract(collection) -> tuple[Path, Path]:
    merged_path = collections_storage.merged_source_path(collection.code, collection.language)
    manifest_path = collections_storage.manifest_path(collection.code, collection.language)
    if not merged_path.exists():
        raise FileNotFoundError("Collection cannot be marked READY_FOR_PIPELINE without merged source.")
    if not manifest_path.exists():
        raise FileNotFoundError("Collection cannot be marked READY_FOR_PIPELINE without manifest.json.")
    return merged_path, manifest_path


def mark_ready_for_pipeline(collection) -> None:
    _require_ready_contract(collection)
    collection.status = COLLECTION_STATUS_READY_FOR_PIPELINE
    collection.save(update_fields=["status", "updated_at"])


def _get_runtime_models():
    from collections_module.models import CollectionArtifact, CollectionRunState

    return CollectionArtifact, CollectionRunState


def _set_item_status(items, *, upload_status: str | None = None, prep_status: str | None = None, normalize_status: str | None = None, merge_status: str | None = None) -> None:
    for item in items:
        update_fields: list[str] = []
        if upload_status is not None:
            item.upload_status = upload_status
            update_fields.append("upload_status")
        if prep_status is not None:
            item.prep_status = prep_status
            update_fields.append("prep_status")
        if normalize_status is not None:
            item.normalize_status = normalize_status
            update_fields.append("normalize_status")
        if merge_status is not None:
            item.merge_status = merge_status
            update_fields.append("merge_status")
        if update_fields:
            item.save(update_fields=update_fields)


def _set_run_started(collection, step: str):
    _CollectionArtifact, CollectionRunState = _get_runtime_models()
    state, _ = CollectionRunState.objects.get_or_create(collection=collection)
    state.current_step = step
    state.last_error = ""
    state.is_locked = True
    state.started_at = timezone.now()
    state.finished_at = None
    state.save()
    return state


def _set_run_failed(collection, state, items, exc: Exception) -> None:
    collection.status = COLLECTION_STATUS_FAILED
    collection.save(update_fields=["status", "updated_at"])
    state.current_step = "failed"
    state.last_error = str(exc)
    state.is_locked = False
    state.finished_at = timezone.now()
    state.save()


def _set_run_finished(state, step: str) -> None:
    state.current_step = step
    state.is_locked = False
    state.finished_at = timezone.now()
    state.save()


def run_prepare(collection, items) -> list:
    CollectionArtifact, _CollectionRunState = _get_runtime_models()
    state = _set_run_started(collection, "prepare")
    ordered_items: list = []
    try:
        ordered_items = validate_collection_items(collection, items)
        if not all(item.source_original_path for item in ordered_items):
            raise ValueError("Collection preparation requires all HTML uploads before execution.")
        _set_item_status(ordered_items, prep_status=ITEM_STATUS_RUNNING)
        results = prepare_collection_items(collection, ordered_items)
        collection.status = COLLECTION_STATUS_PREPARED
        collection.save(update_fields=["status", "updated_at"])
        for item in ordered_items:
            item.upload_status = ITEM_STATUS_COMPLETED if item.source_original_path else ITEM_STATUS_PENDING
            item.prep_status = ITEM_STATUS_COMPLETED
            item.normalize_status = ITEM_STATUS_PENDING
            item.save(update_fields=["upload_status", "prep_status", "normalize_status"])
        for result in results:
            CollectionArtifact.objects.update_or_create(
                collection=collection,
                artifact_type=f"upload_item_{result.order_index:02d}",
                language=collection.language,
                path=str(result.upload_path),
                defaults={"sha256": result.upload_sha256},
            )
            CollectionArtifact.objects.update_or_create(
                collection=collection,
                artifact_type=f"prepared_item_{result.order_index:02d}",
                language=collection.language,
                path=str(result.prepared_path),
                defaults={"sha256": result.prepared_sha256},
            )
        write_manifest(collection, ordered_items)
        _set_run_finished(state, "prepared")
        return results
    except Exception as exc:
        _set_item_status(ordered_items, prep_status=ITEM_STATUS_FAILED)
        _set_run_failed(collection, state, ordered_items, exc)
        raise


def run_normalize(collection, items) -> list:
    CollectionArtifact, _CollectionRunState = _get_runtime_models()
    state = _set_run_started(collection, "normalize")
    ordered_items: list = []
    try:
        ordered_items = validate_collection_items(collection, items)
        if not all(item.prep_status == ITEM_STATUS_COMPLETED for item in ordered_items):
            raise ValueError("Collection normalize requires all items prepared first.")
        _set_item_status(ordered_items, normalize_status=ITEM_STATUS_RUNNING)
        results = normalize_collection_items(collection, ordered_items)
        collection.status = COLLECTION_STATUS_NORMALIZED
        collection.save(update_fields=["status", "updated_at"])
        for item in ordered_items:
            item.normalize_status = ITEM_STATUS_COMPLETED
            item.save(update_fields=["normalize_status"])
        for result in results:
            CollectionArtifact.objects.update_or_create(
                collection=collection,
                artifact_type=f"normalized_item_{result.order_index:02d}",
                language=collection.language,
                path=str(result.normalized_path),
                defaults={"sha256": result.normalized_sha256},
            )
        write_manifest(collection, ordered_items)
        _set_run_finished(state, "normalized")
        return results
    except Exception as exc:
        _set_item_status(ordered_items, normalize_status=ITEM_STATUS_FAILED)
        _set_run_failed(collection, state, ordered_items, exc)
        raise


def run_merge(collection, items) -> Path:
    CollectionArtifact, _CollectionRunState = _get_runtime_models()
    state = _set_run_started(collection, "merge")
    ordered_items: list = []
    try:
        ordered_items = validate_collection_items(collection, items)
        if not all(item.normalize_status == ITEM_STATUS_COMPLETED for item in ordered_items):
            raise ValueError("Collection merge requires all items normalized first.")
        _set_item_status(ordered_items, merge_status=ITEM_STATUS_RUNNING)
        merged_path = merge_collection_items(collection, ordered_items)
        collection.status = COLLECTION_STATUS_MERGED
        collection.save(update_fields=["status", "updated_at"])
        _set_item_status(ordered_items, merge_status=ITEM_STATUS_COMPLETED)
        CollectionArtifact.objects.update_or_create(
            collection=collection,
            artifact_type="merged_source",
            language=collection.language,
            path=str(merged_path),
            defaults={"sha256": _sha256_file(merged_path)},
        )
        write_manifest(collection, ordered_items, merged_path)
        _set_run_finished(state, "merged")
        return merged_path
    except Exception as exc:
        collection.status = COLLECTION_STATUS_FAILED
        collection.save(update_fields=["status", "updated_at"])
        _set_item_status(ordered_items, merge_status=ITEM_STATUS_FAILED)
        state.current_step = "failed"
        state.last_error = str(exc)
        state.is_locked = False
        state.finished_at = timezone.now()
        state.save()
        raise


def _next_pipeline_book_code() -> str:
    from editorial.models import Work

    used = set(Work.objects.values_list("code", flat=True))
    for index in range(7000, 9999):
        candidate = f"book_{index:04d}"
        if candidate not in used:
            return candidate
    raise RuntimeError("No free pipeline book code available for collection handoff.")


def _ensure_pipeline_runtime_defaults() -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            """
            ALTER TABLE work
              ALTER COLUMN subtitle SET DEFAULT '',
              ALTER COLUMN enabled_languages SET DEFAULT '[]'::jsonb,
              ALTER COLUMN source_format SET DEFAULT 'txt',
              ALTER COLUMN notes SET DEFAULT ''
            """
        )
        cursor.execute(
            """
            ALTER TABLE edition
              ALTER COLUMN language_variant SET DEFAULT '',
              ALTER COLUMN copyright_text SET DEFAULT '',
              ALTER COLUMN editorial_name SET DEFAULT '',
              ALTER COLUMN edition_copyright_holder SET DEFAULT '',
              ALTER COLUMN book_id SET DEFAULT '',
              ALTER COLUMN canonical_official_tag SET DEFAULT '',
              ALTER COLUMN canonical_run_dir SET DEFAULT '',
              ALTER COLUMN lang SET DEFAULT '',
              ALTER COLUMN raw_materialized_path SET DEFAULT '',
              ALTER COLUMN raw_sha256 SET DEFAULT '',
              ALTER COLUMN status SET DEFAULT '',
              ALTER COLUMN truth_path SET DEFAULT '',
              ALTER COLUMN truth_sha256 SET DEFAULT ''
            """
        )


def handoff_to_pipeline(collection, items):
    from collections_module.models import CollectionArtifact
    from editorial.models import Contributor, Edition, EditionPipeline, EditionText, Language, Seal, Work
    from pipeline.models import BookEditionTemplate

    ordered_items = validate_collection_items(collection, items)
    merged_path, _manifest_path = _require_ready_contract(collection)
    if not can_handoff_to_pipeline(collection.status):
        raise ValueError("Pipeline handoff is allowed only after COLLECTION_MERGED.")
    if collection.status == COLLECTION_STATUS_MERGED:
        mark_ready_for_pipeline(collection)
    _ensure_pipeline_runtime_defaults()

    language, _ = Language.objects.get_or_create(
        code=collection.language,
        defaults={"name": collection.language.upper(), "native_name": collection.language.upper(), "is_active": True},
    )
    contributor, _ = Contributor.objects.get_or_create(name=collection.author_display_name, defaults={"role": "AUTHOR"})
    seal, _ = Seal.objects.get_or_create(slug="mantaquest", defaults={"name": "MantaQuest"})
    pipeline_book_code = collection.pipeline_book_code or _next_pipeline_book_code()
    work, _ = Work.objects.get_or_create(
        code=pipeline_book_code,
        defaults={
            "title": collection.title,
            "original_language": language,
            "author": contributor,
            "is_public_domain": True,
        },
    )
    edition, _ = Edition.objects.get_or_create(
        work=work,
        language=language,
        seal=seal,
        defaults={
            "title": collection.title,
            "subtitle": collection.subtitle,
            "author": collection.author_display_name,
            "raw_source_path": str(merged_path),
        },
    )
    EditionText.objects.update_or_create(
        edition=edition,
        defaults={"raw_path": str(merged_path), "raw_text": merged_path.read_text(encoding="utf-8")},
    )
    EditionPipeline.objects.update_or_create(
        edition=edition,
        defaults={"current_stage": "RAW"},
    )
    BookEditionTemplate.objects.update_or_create(
        book_code=pipeline_book_code,
        language=collection.language,
        defaults={
            "title": collection.title,
            "subtitle": collection.subtitle,
            "author_name": collection.author_display_name,
            "publication_year": timezone.now().year,
            "collection_name": collection.title,
        },
    )
    CollectionArtifact.objects.update_or_create(
        collection=collection,
        artifact_type="pipeline_source",
        language=collection.language,
        path=str(merged_path),
        defaults={"sha256": _sha256_file(merged_path)},
    )
    collection.pipeline_book_code = pipeline_book_code
    collection.status = COLLECTION_STATUS_PIPELINE_RUNNING
    collection.save(update_fields=["pipeline_book_code", "status", "updated_at"])
    write_manifest(collection, ordered_items, merged_path)
    return edition
