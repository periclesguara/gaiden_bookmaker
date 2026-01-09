from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Tuple, Optional

from pypdf import PdfReader
from docx import Document

UPLOADS_DIR = Path("data/uploads")
ALLOWED_EXT = {"txt", "md", "pdf", "docx"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_upload(data: bytes, original_filename: str) -> Tuple[Path, str, str]:
    """
    Salva arquivo em data/uploads/<sha256>.<ext>
    Retorna (stored_path, ext, sha256)
    """
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    ext = (original_filename.split(".")[-1] if "." in original_filename else "").lower().strip()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"Extensão não suportada: .{ext or '?'} | Aceitas: {sorted(ALLOWED_EXT)}")

    digest = sha256_bytes(data)
    stored = UPLOADS_DIR / f"{digest}.{ext}"
    stored.write_bytes(data)
    return stored, ext, digest


def extract_text_from_file(path: Path, ext: str) -> Optional[str]:
    """
    Extrai texto (best-effort).
    TXT/MD: ok
    DOCX: ok
    PDF: depende (PDF escaneado geralmente vem vazio).
    """
    ext = (ext or "").lower().strip()

    try:
        if ext in ("txt", "md"):
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            return text or None

        if ext == "pdf":
            reader = PdfReader(path.as_posix())
            parts = []
            for page in reader.pages:
                parts.append(page.extract_text() or "")
            text = "\n".join(parts).strip()
            return text or None

        if ext == "docx":
            doc = Document(path.as_posix())
            parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
            text = "\n".join(parts).strip()
            return text or None

        return None
    except Exception:
        return None
