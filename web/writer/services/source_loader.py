from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..models import SourceDocument


@dataclass(frozen=True)
class RawDocument:
    source_type: str
    text: str
    title: str = ""
    external_id: str = ""
    external_url: str = ""
    revision_id: str = ""
    modified_at: datetime | None = None
    structure: tuple | list = ()
    metadata: dict = field(default_factory=dict)


def _load_local(document: SourceDocument) -> RawDocument:
    source = Path(document.source_path).expanduser()
    if source.is_symlink():
        raise ValueError("symlink sources are forbidden")
    source = source.resolve(strict=True)
    if source.suffix.casefold() not in {".txt", ".md"}:
        raise ValueError("only direct .txt and .md source files are supported")
    return RawDocument(
        source_type=SourceDocument.SourceType.LOCAL_FILE,
        text=source.read_text(encoding="utf-8", errors="strict"),
        title=document.filename,
    )


def load_source(document: SourceDocument) -> RawDocument:
    if document.source_type == SourceDocument.SourceType.GOOGLE_DOC:
        from .google_docs import load_google_doc
        return load_google_doc(document)
    return _load_local(document)
