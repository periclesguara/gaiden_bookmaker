from __future__ import annotations

from pathlib import Path

from django.utils import timezone

from gaiden.application.collections import service as collection_service
from gaiden.domain.editorial.collections import COLLECTION_STATUS_CREATED, ITEM_STATUS_COMPLETED, validate_contiguous_order, validate_no_duplicates
from gaiden.infrastructure import collections_storage

from collections_module.models import Collection, CollectionItem, CollectionRunState
from collections_module.services import image_maker, pre_images


def next_collection_code() -> str:
    last = Collection.objects.order_by("-id").first()
    next_id = 1 if last is None else last.id + 1
    return f"collection_{next_id:04d}"


def create_collection(*, title: str, subtitle: str, collection_kind: str, author_display_name: str, language: str, item_count: int) -> Collection:
    collection = Collection.objects.create(
        code=next_collection_code(),
        title=title,
        subtitle=subtitle,
        collection_kind=collection_kind,
        author_display_name=author_display_name,
        language=language,
        item_count=item_count,
        status=COLLECTION_STATUS_CREATED,
    )
    collections_storage.ensure_collection_layout(collection.code, collection.language)
    CollectionRunState.objects.get_or_create(collection=collection)
    return collection


def _ordered_items(collection: Collection) -> list[CollectionItem]:
    return list(collection.items.filter(is_active=True).order_by("order_index"))


def build_collection_context(collection: Collection) -> dict:
    merged_path = collections_storage.merged_source_path(collection.code, collection.language)
    pipeline_edition = None
    if collection.pipeline_book_code:
        try:
            from editorial.models import Edition

            pipeline_edition = Edition.objects.filter(
                work__code=collection.pipeline_book_code,
                language__code=collection.language,
            ).first()
        except Exception:
            pipeline_edition = None
    try:
        run_state = collection.run_state
    except CollectionRunState.DoesNotExist:
        run_state = None
    return {
        "collection": collection,
        "items": _ordered_items(collection),
        "merged_path": merged_path,
        "merged_exists": merged_path.exists(),
        "pipeline_edition": pipeline_edition,
        "run_state": run_state,
        "pre_images": pre_images.pre_images_status(collection),
        "image_maker": image_maker.image_maker_status(collection),
        "translate_options": [
            {
                "scope": "Book + Collection",
                "target": "EN (modern)",
                "route": "Agent",
                "agent": "HeadingCleaner",
                "notes": "Tradutor geral ativo para os splits que vao para ingles.",
            },
            {
                "scope": "Book + Collection",
                "target": "FR",
                "route": "Agent",
                "agent": "fr_translate_universal_2026",
                "notes": "Tradutor interno ativo para frances no stage Translate, tanto em Book quanto em Collection.",
            },
            {
                "scope": "Book + Collection",
                "target": "ES, PT-BR, DE, IT",
                "route": "Placeholder",
                "agent": "not configured yet",
                "notes": "Sem tradutor registrado ainda; nao aparece na etapa Translate.",
            },
        ],
    }


def merged_preview(collection: Collection, *, limit: int = 8000) -> str:
    merged_path = collections_storage.merged_source_path(collection.code, collection.language)
    if not merged_path.exists():
        return ""
    return merged_path.read_text(encoding="utf-8")[:limit]


def register_items(collection: Collection) -> None:
    items = _ordered_items(collection)
    validate_contiguous_order([item.order_index for item in items])
    validate_no_duplicates([(item.author_name.strip().lower(), item.work_title.strip().lower()) for item in items])
    if len(items) >= 2:
        collection_service.items_registered(collection, item_count=len(items))


def store_collection_upload(collection: Collection, item: CollectionItem, uploaded_file) -> Path:
    upload_path = collections_storage.item_upload_path(collection.code, collection.language, item.order_index, uploaded_file.name)
    collections_storage.ensure_collection_layout(collection.code, collection.language)
    with upload_path.open("wb") as fh:
        for chunk in uploaded_file.chunks():
            fh.write(chunk)
    item.source_filename = uploaded_file.name
    item.source_original_path = str(upload_path)
    item.uploaded_at = timezone.now()
    item.upload_status = ITEM_STATUS_COMPLETED
    item.save(update_fields=["source_filename", "source_original_path", "uploaded_at", "upload_status"])
    items = _ordered_items(collection)
    if items and all(current.source_original_path for current in items):
        collection_service.collection_uploads_received(collection, item_count=len(items))
    return upload_path


def run_prepare(collection: Collection) -> list:
    return collection_service.run_prepare(collection, _ordered_items(collection))


def run_normalize(collection: Collection) -> list:
    return collection_service.run_normalize(collection, _ordered_items(collection))


def run_merge(collection: Collection) -> Path:
    return collection_service.run_merge(collection, _ordered_items(collection))


def handoff_to_pipeline(collection: Collection):
    return collection_service.handoff_to_pipeline(collection, _ordered_items(collection))


def run_pre_images(collection: Collection) -> dict:
    return pre_images.run_pre_images(collection, _ordered_items(collection))


def validate_image_maker(collection: Collection, raw_package: str = "") -> dict:
    return image_maker.validate_rules(collection, raw_package)


def build_image_maker_jobs(collection: Collection, raw_package: str = "") -> dict:
    return image_maker.build_jobs(collection, raw_package)


def dry_run_image_maker(collection: Collection) -> dict:
    return image_maker.dry_run_generation(collection)
