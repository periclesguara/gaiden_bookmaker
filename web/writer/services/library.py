"""Expose existing folders without discovery writes, transfer or transformation."""
import os
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.db.models import Prefetch

from editorial.models import Edition
from pipeline.models import IntakeBatch, IntakeItem

from ..models import SourceDocument
from .sources import source_root


def existing_library():
    registered = IntakeItem.objects.exclude(canonical_path="")
    batches = IntakeBatch.objects.filter(items__in=registered).distinct().prefetch_related(
        Prefetch("items", queryset=registered.order_by("relative_path"), to_attr="library_items")
    ).order_by("name", "batch_code")
    local_folders = defaultdict(list)
    error = ""
    if os.environ.get("GAIDEN_WRITER_SOURCE_ROOT", "").strip():
        try:
            root = source_root()
            for directory, dirs, files in os.walk(root, followlinks=False):
                parent = Path(directory)
                dirs[:] = sorted(name for name in dirs if not (parent / name).is_symlink())
                for name in sorted(files):
                    path = parent / name
                    if path.is_symlink() or not path.is_file():
                        continue
                    local_folders[str(parent.relative_to(root))].append({"name": name, "path": str(path)})
        except (OSError, ValueError):
            error = "O diretório de fontes locais configurado está indisponível. Nenhum arquivo foi alterado."
    return {
        "batches": list(batches),
        "registered_documents": SourceDocument.objects.prefetch_related("projects").all(),
        "edition_sources": Edition.objects.select_related("work", "language")
            .exclude(raw_source_path="").order_by("work__code", "id"),
        "local_folders": dict(sorted(local_folders.items())),
        "library_error": error,
        "drive_inbox": settings.GAIDEN_DRIVE_INBOX,
        "drive_imported": settings.GAIDEN_DRIVE_IMPORTED,
    }
