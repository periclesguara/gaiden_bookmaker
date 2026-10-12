from __future__ import annotations

from pathlib import Path
from typing import Any


def build_markitdown_report(
    *,
    book_code: str,
    lang: str,
    source_path: str | Path,
    raw_markdown_path: str | Path,
    clean_markdown_path: str | Path,
    promoted_markdown_path: str | Path | None,
    markdown_text: str,
    headings_count: int,
    status: str,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "gaiden.markitdown_report.v1",
        "book_code": book_code,
        "lang": lang,
        "source_path": str(Path(source_path)),
        "raw_markdown_path": str(Path(raw_markdown_path)),
        "clean_markdown_path": str(Path(clean_markdown_path)),
        "promoted_markdown_path": str(Path(promoted_markdown_path)) if promoted_markdown_path else None,
        "markdown_chars": len(markdown_text),
        "headings_count": headings_count,
        "status": status,
        "warnings": warnings or [],
        "errors": errors or [],
    }
