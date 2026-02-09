from __future__ import annotations

"""
DEPRECATED: use gaiden.chunk_engine (canonical chunking engine).
This module remains as a thin wrapper for legacy callers.
"""

from pathlib import Path
from typing import Any

from gaiden.chunk_engine import run_chunking


def build_chapter_chunks(
    raw_text: str,
    output_dir: Path,
    manifest_path: Path | None = None,
    language: str = "en",
    min_tokens: int = 1500,  # unused; kept for signature compatibility
    target_tokens: int = 1500,
    max_tokens: int = 2000,
) -> dict[str, Any]:
    if language != "en":
        raise ValueError("Chunking é EN-only e compartilhado entre línguas destino.")

    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_path = output_dir / "_normalized_input.tmp.txt"
    normalized_path.write_text(raw_text, encoding="utf-8")

    result = run_chunking(
        book_code="book_legacy",
        lang="en",
        normalized_path=normalized_path,
        out_dir=output_dir,
        target_tokens=target_tokens,
        max_tokens=max_tokens,
        dry_run=False,
    )

    if manifest_path:
        # Mirror canonical manifest to the requested legacy path.
        manifest_path.write_text(
            (output_dir / "chunks_manifest.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    return {
        "normalized_text": raw_text,
        "manifest": result.get("manifest"),
        "manifest_path": str(manifest_path) if manifest_path else "",
    }
