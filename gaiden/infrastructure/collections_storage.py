from __future__ import annotations

from pathlib import Path

from . import storage

COLLECTION_NAMESPACE = "collections"
UPLOADS_DIRNAME = "uploads"
PREPARED_DIRNAME = "prepared"
NORMALIZED_ITEMS_DIRNAME = "normalized_items"
MERGED_DIRNAME = "merged"
AUDIT_DIRNAME = "audit"
FRONTMATTER_DIRNAME = "frontmatter"
MD_DIRNAME = "md"
BUILD_DIRNAME = "build"
PRE_IMAGES_DIRNAME = "pre_images"
IMAGE_MAKER_DIRNAME = "image_maker"


def collection_root(collection_code: str, language: str) -> Path:
    return storage.data_dir() / COLLECTION_NAMESPACE / collection_code / language


def uploads_dir(collection_code: str, language: str) -> Path:
    return collection_root(collection_code, language) / UPLOADS_DIRNAME


def prepared_dir(collection_code: str, language: str) -> Path:
    return collection_root(collection_code, language) / PREPARED_DIRNAME


def normalized_items_dir(collection_code: str, language: str) -> Path:
    return collection_root(collection_code, language) / NORMALIZED_ITEMS_DIRNAME


def merged_dir(collection_code: str, language: str) -> Path:
    return collection_root(collection_code, language) / MERGED_DIRNAME


def frontmatter_dir(collection_code: str, language: str) -> Path:
    return collection_root(collection_code, language) / FRONTMATTER_DIRNAME


def md_dir(collection_code: str, language: str) -> Path:
    return collection_root(collection_code, language) / MD_DIRNAME


def build_dir(collection_code: str, language: str) -> Path:
    return collection_root(collection_code, language) / BUILD_DIRNAME


def pre_images_dir(collection_code: str, language: str) -> Path:
    return collection_root(collection_code, language) / PRE_IMAGES_DIRNAME


def image_maker_dir(collection_code: str, language: str) -> Path:
    return collection_root(collection_code, language) / IMAGE_MAKER_DIRNAME


def audit_dir(collection_code: str, language: str) -> Path:
    return collection_root(collection_code, language) / AUDIT_DIRNAME


def ensure_collection_layout(collection_code: str, language: str) -> Path:
    root = collection_root(collection_code, language)
    for path in (
        uploads_dir(collection_code, language),
        prepared_dir(collection_code, language),
        normalized_items_dir(collection_code, language),
        merged_dir(collection_code, language),
        audit_dir(collection_code, language),
        pre_images_dir(collection_code, language),
        image_maker_dir(collection_code, language),
    ):
        path.mkdir(parents=True, exist_ok=True)
    return root


def manifest_path(collection_code: str, language: str) -> Path:
    return collection_root(collection_code, language) / "manifest.json"


def merged_source_path(collection_code: str, language: str) -> Path:
    return merged_dir(collection_code, language) / f"{collection_code}_{language}_source.txt"


def item_upload_path(collection_code: str, language: str, order_index: int, filename: str) -> Path:
    safe_name = Path(filename).name
    return uploads_dir(collection_code, language) / f"item_{order_index:02d}_{safe_name}"


def item_prepared_path(collection_code: str, language: str, order_index: int) -> Path:
    return prepared_dir(collection_code, language) / f"item_{order_index:02d}_prepared.txt"


def item_normalized_path(collection_code: str, language: str, order_index: int) -> Path:
    return normalized_items_dir(collection_code, language) / f"item_{order_index:02d}_normalized.txt"
