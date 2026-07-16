from __future__ import annotations

import os
from pathlib import Path

from gaiden.infrastructure import storage

LEGACY_DATA_ROOT_ENV = "GAIDEN_DATA_ROOT"


def get_data_root(*, must_exist: bool = False) -> Path:
    configured = (os.environ.get(LEGACY_DATA_ROOT_ENV) or "").strip()
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute():
            root = storage.repo_root() / root
        root = root.resolve()
        if must_exist and not root.exists():
            raise storage.StorageConfigurationError(f"Data root does not exist: {root}")
        return root
    return storage.storage_root(must_exist=must_exist)


def validate_data_root() -> Path:
    root = get_data_root(must_exist=True)
    deprecated_root = storage.deprecated_web_storage_root().resolve()
    if root == deprecated_root or deprecated_root in root.parents:
        raise storage.StorageConfigurationError(f"Deprecated web storage root is not allowed: {root}")
    return root


def get_book_markitdown_dir(book_code: str, lang: str) -> Path:
    return get_data_root() / "preprod" / book_code / lang / "markitdown"


def get_book_md_dir(book_code: str, lang: str) -> Path:
    return get_data_root() / "md" / book_code / lang


def get_book_source_md_path(book_code: str, lang: str) -> Path:
    return get_book_md_dir(book_code, lang) / f"{book_code}_{lang}_source.md"
