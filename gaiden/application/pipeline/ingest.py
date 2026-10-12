from __future__ import annotations

import hashlib
import re
from html import unescape
from pathlib import Path
from typing import Optional, Tuple

from gaiden.infrastructure import storage
from gaiden.application.pipeline.source_extract import (
    build_reading_preview,
    run_source_extract,
    supported_extensions as source_extract_supported_extensions,
)

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None

try:
    from docx import Document
except ImportError:  # pragma: no cover
    Document = None

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None

ALLOWED_EXT = {"txt", "md", "pdf", "docx", "html", "htm", "epub"}
SOURCE_EXTRACT_ALLOWED_EXT = {ext.lstrip(".") for ext in source_extract_supported_extensions()}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_upload(data: bytes, original_filename: str) -> Tuple[Path, str, str]:
    uploads_dir = storage.uploads_dir()
    uploads_dir.mkdir(parents=True, exist_ok=True)

    ext = (original_filename.split(".")[-1] if "." in original_filename else "").lower().strip()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"Extensão não suportada: .{ext or '?'} | Aceitas: {sorted(ALLOWED_EXT)}")

    digest = sha256_bytes(data)
    stored = uploads_dir / f"{digest}.{ext}"
    stored.write_bytes(data)
    return stored, ext, digest


def extract_text_from_file(path: Path, ext: str) -> Optional[str]:
    ext = (ext or "").lower().strip()
    try:
        if ext in ("txt", "md"):
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            return text or None
        if ext in ("html", "htm"):
            return extract_text_from_html(path)
        if ext == "pdf":
            if PdfReader is None:
                return None
            reader = PdfReader(path.as_posix())
            parts = [(page.extract_text() or "") for page in reader.pages]
            text = "\n".join(parts).strip()
            return text or None
        if ext == "docx":
            if Document is None:
                return None
            doc = Document(path.as_posix())
            parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
            text = "\n".join(parts).strip()
            return text or None
        return None
    except Exception:
        return None


def extract_text_from_html(path: Path) -> Optional[str]:
    raw_html = path.read_text(encoding="utf-8", errors="replace")
    if BeautifulSoup is not None:
        soup = BeautifulSoup(raw_html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text("\n")
    else:
        text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", raw_html)
        text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", "", text)
        text = re.sub(r"(?s)<!--.*?-->", "", text)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</(p|div|li|h[1-6]|section|article|blockquote|tr|td|th|ul|ol)>", "\n", text)
        text = re.sub(r"(?s)<[^>]+>", "", text)
        text = unescape(text)

    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    return cleaned or None
