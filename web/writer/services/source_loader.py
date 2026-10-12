from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import zipfile
from django.conf import settings

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
    size_limit = getattr(settings, "GAIDEN_INTAKE_MAX_FILE_SIZE", 100 * 1024 * 1024)
    if source.stat().st_size > size_limit:
        raise ValueError("Arquivo de referência excede o limite de tamanho.")
    suffix = source.suffix.casefold()
    if suffix in {".txt", ".md", ".markdown"}:
        text = source.read_text(encoding="utf-8", errors="strict")
    elif suffix in {".html", ".htm", ".xhtml"}:
        from gaiden.infrastructure.source_extractors.html_extractor import html_to_text
        text = html_to_text(source.read_text(encoding="utf-8", errors="strict"))
    elif suffix == ".epub":
        from gaiden.infrastructure.source_extractors.epub_reader import EpubReader
        from gaiden.infrastructure.source_extractors.epub_extractor import COSMETIC_FILENAMES, _canonicalize_structural_chapter_heading, _is_html_item
        from gaiden.infrastructure.source_extractors.html_extractor import html_to_text
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > 10000 or sum(item.file_size for item in entries) > size_limit:
                raise ValueError("EPUB de referência excede o limite descompactado.")
        package = EpubReader(source).read()
        with zipfile.ZipFile(source) as archive:
            parts = [html_to_text(_canonicalize_structural_chapter_heading(
                archive.read(item["path"]).decode("utf-8", errors="strict")))
                for item in package.spine if _is_html_item(item)
                and Path(item["path"]).name.lower() not in COSMETIC_FILENAMES]
        text = "\n\n".join(parts)
    else:
        raise ValueError("Formato de referência não suportado: use TXT, Markdown, HTML ou EPUB.")
    return RawDocument(
        source_type=SourceDocument.SourceType.LOCAL_FILE,
        text=text,
        title=document.filename,
    )


def load_source(document: SourceDocument) -> RawDocument:
    if document.source_type == SourceDocument.SourceType.GOOGLE_DOC:
        from .google_docs import load_google_doc
        return load_google_doc(document)
    return _load_local(document)
