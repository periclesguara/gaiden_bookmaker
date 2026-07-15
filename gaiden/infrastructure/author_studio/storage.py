from __future__ import annotations

from pathlib import Path

from django.utils.text import get_valid_filename

from gaiden.infrastructure.storage import storage_root


def author_studio_root() -> Path:
    return storage_root() / "author_studio"


def source_upload_path(instance, filename: str) -> str:
    safe_name = get_valid_filename(Path(filename).name) or "source"
    work = instance.work
    return str(Path("author_studio/authors") / work.author.code / "works" / work.code / "sources" / instance.code / safe_name)


def canonical_upload_path(instance, filename: str) -> str:
    return str(Path("author_studio/authors") / instance.work.author.code / "works" / instance.work.code / "canonical" / instance.code / "canonical.txt")


def chunk_upload_path(instance, filename: str) -> str:
    return str(
        Path("author_studio/authors")
        / instance.work.author.code
        / "works"
        / instance.work.code
        / "chunks"
        / instance.code
        / "chunk.txt"
    )
