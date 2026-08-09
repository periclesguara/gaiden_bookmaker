from __future__ import annotations

import hashlib
import os
from pathlib import Path

from author_studio.models import CanonicalText

from gaiden.domain.author_studio.enums import CanonicalTextStatus

from ..models import SourceDocument


def source_root() -> Path:
    configured = os.environ.get("GAIDEN_WRITER_SOURCE_ROOT", "").strip()
    if not configured:
        raise ValueError("GAIDEN_WRITER_SOURCE_ROOT is not configured")
    root = Path(configured).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("GAIDEN_WRITER_SOURCE_ROOT is not a directory")
    return root


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_filename(canonical: CanonicalText, suffix: str) -> str:
    label = f"{canonical.work.code} - {canonical.work.title}"
    return f"{label[: 255 - len(suffix)]}{suffix}"


def _discover_author_studio_canonicals() -> int:
    count = 0
    canonicals = CanonicalText.objects.select_related("work").filter(
        status=CanonicalTextStatus.READY.value
    )
    for canonical in canonicals.order_by("work__code"):
        configured_path = Path(canonical.text_file.path).expanduser()
        if configured_path.is_symlink():
            raise ValueError(f"canonical source is a forbidden symlink: {canonical.work.code}")
        path = configured_path.resolve(strict=True)
        if not path.is_file() or path.suffix.casefold() not in {".txt", ".md"}:
            raise ValueError(f"canonical source is not supported: {canonical.work.code}")
        source_sha256 = _sha256_file(path)
        if source_sha256 != canonical.sha256:
            raise ValueError(f"canonical checksum mismatch: {canonical.work.code}")
        _, created = SourceDocument.objects.get_or_create(
            source_path=str(path),
            defaults={
                "filename": _canonical_filename(canonical, path.suffix.casefold()),
                "source_sha256": source_sha256,
                "normalized_path": str(path),
                "normalized_sha256": source_sha256,
                "provider": "AUTHOR_STUDIO",
                "status": SourceDocument.Status.NORMALIZED,
                "normalized_at": canonical.updated_at,
                "normalization_report": {
                    "rules": ["author-studio-canonical-reuse"],
                    "removed_characters": 0,
                    "source": "author_studio.CanonicalText",
                    "work_code": canonical.work.code,
                },
            },
        )
        count += int(created)
    return count


def discover_source_documents() -> int:
    count = _discover_author_studio_canonicals()
    configured = os.environ.get("GAIDEN_WRITER_SOURCE_ROOT", "").strip()
    if not configured:
        return count
    root = source_root()
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
