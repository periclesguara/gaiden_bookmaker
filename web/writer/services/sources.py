from __future__ import annotations

import os
from pathlib import Path

from writer.models import SourceDocument


def source_root() -> Path:
    configured = os.environ.get("GAIDEN_WRITER_SOURCE_ROOT", "").strip()
    if not configured:
        raise ValueError("GAIDEN_WRITER_SOURCE_ROOT is not configured")
    root = Path(configured).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("GAIDEN_WRITER_SOURCE_ROOT is not a directory")
    return root


def discover_source_documents() -> int:
    root = source_root()
    count = 0
    for path in sorted(root.rglob("*")):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.is_symlink():
            raise ValueError(f"symlink source is forbidden: {path}")
        if not path.is_file() or path.suffix.casefold() not in {".txt", ".md"}:
            continue
        _, created = SourceDocument.objects.get_or_create(
            source_path=str(path.resolve()),
            defaults={"filename": path.name},
        )
        count += int(created)
    return count
