from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

CANONICAL_STORAGE_ENV = "GAIDEN_STORAGE_ROOT"
DEFAULT_STORAGE_DIRNAME = "data"
DEPRECATED_WEB_STORAGE_DIRNAME = "web/data"


class StorageConfigurationError(RuntimeError):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def web_root() -> Path:
    return repo_root() / "web"


def deprecated_web_storage_root() -> Path:
    return repo_root() / DEPRECATED_WEB_STORAGE_DIRNAME


def storage_root(*, must_exist: bool = False) -> Path:
    configured = (os.environ.get(CANONICAL_STORAGE_ENV) or "").strip()
    root = Path(configured).expanduser() if configured else (repo_root() / DEFAULT_STORAGE_DIRNAME)
    if not root.is_absolute():
        root = repo_root() / root
    root = root.resolve()
    if must_exist and not root.exists():
        raise StorageConfigurationError(
            f"Canonical storage root does not exist: {root} "
            f"(set {CANONICAL_STORAGE_ENV} or create {repo_root() / DEFAULT_STORAGE_DIRNAME})"
        )
    return root


def resolve_repo_path(path_value: str | Path) -> Path:
    candidate = Path(path_value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (repo_root() / candidate).resolve()


def resolve_storage_path(path_value: str | Path) -> Path:
    candidate = Path(path_value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (repo_root() / candidate).resolve()


def relative_to_repo(path_value: str | Path) -> Path:
    path = resolve_repo_path(path_value)
    try:
        return path.relative_to(repo_root())
    except ValueError as exc:
        raise StorageConfigurationError(f"Path escapes repository root: {path}") from exc


def data_dir() -> Path:
    return storage_root()


def builds_dir(book_code: str | None = None, language: str | None = None) -> Path:
    path = data_dir() / "builds"
    if book_code:
        path /= book_code
    if language:
        path /= language
    return path


def raw_dir(book_code: str | None = None) -> Path:
    path = data_dir() / "raw"
    if book_code:
        path /= book_code
    return path


def raw_source_path(book_code: str, language: str, suffix: str) -> Path:
    return raw_dir(book_code) / f"{book_code}_{language}_raw{suffix}"


def normalized_path(book_code: str, language: str) -> Path:
    return data_dir() / "normalized" / f"{book_code}_{language}_v2.txt"


def chunks_dir(book_code: str | None = None, stage: str | None = None) -> Path:
    path = data_dir() / "chunks"
    if book_code:
        path /= book_code
    if stage:
        path /= stage
    return path


def split_01_dir(book_code: str) -> Path:
    return chunks_dir(book_code, "split_01")


def heading_cleaner_dir(book_code: str) -> Path:
    return chunks_dir(book_code, "heading_cleaner")


def translated_dir(book_code: str | None = None, language_variant: str | None = None) -> Path:
    path = data_dir() / "translated"
    if book_code:
        path /= book_code
    if language_variant:
        path /= language_variant
    return path


def frontmatter_dir(book_code: str | None = None, language: str | None = None) -> Path:
    path = data_dir() / "frontmatter"
    if book_code:
        path /= book_code
    if language:
        path /= language
    return path


def covers_dir(book_code: str | None = None, language: str | None = None) -> Path:
    path = data_dir() / "covers"
    if book_code:
        path /= book_code
    if language:
        path /= language
    return path


def images_dir(book_code: str | None = None, language: str | None = None) -> Path:
    path = data_dir() / "images"
    if book_code:
        path /= book_code
    if language:
        path /= language
    return path


def md_dir(book_code: str | None = None, language: str | None = None) -> Path:
    path = data_dir() / "md"
    if book_code:
        path /= book_code
    if language:
        path /= language
    return path


def preprod_dir(book_code: str | None = None, language: str | None = None) -> Path:
    path = data_dir() / "preprod"
    if book_code:
        path /= book_code
    if language:
        path /= language
    return path


def editions_dir(edition_id: int | str | None = None) -> Path:
    path = data_dir() / "editions"
    if edition_id is not None:
        path /= str(edition_id)
    return path


def db_path(filename: str = "gaiden.sqlite3") -> Path:
    return data_dir() / "db" / filename


def uploads_dir() -> Path:
    return data_dir() / "uploads"


def tmp_dir(name: str | None = None) -> Path:
    path = data_dir() / "tmp"
    if name:
        path /= name
    return path


def repo_contract_path(relative_path: str | Path) -> Path:
    return resolve_repo_path(relative_path)


@dataclass(frozen=True)
class StorageDiagnostic:
    canonical_root: Path
    deprecated_web_root: Path
    deprecated_web_root_exists: bool
    deprecated_web_root_has_runtime_files: bool


def storage_diagnostic() -> StorageDiagnostic:
    deprecated_root = deprecated_web_storage_root()
    runtime_files = any(
        path.is_file() and path.name != "README.md"
        for path in deprecated_root.rglob("*")
    ) if deprecated_root.exists() else False
    return StorageDiagnostic(
        canonical_root=storage_root(),
        deprecated_web_root=deprecated_root,
        deprecated_web_root_exists=deprecated_root.exists(),
        deprecated_web_root_has_runtime_files=runtime_files,
    )


def validate_storage_layout() -> StorageDiagnostic:
    diagnostic = storage_diagnostic()
    if diagnostic.deprecated_web_root_has_runtime_files:
        raise StorageConfigurationError(
            "Deprecated web/data runtime files detected. "
            f"Canonical root is {diagnostic.canonical_root}; migrate files out of {diagnostic.deprecated_web_root}."
        )
    return diagnostic
