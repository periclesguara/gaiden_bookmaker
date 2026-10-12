from __future__ import annotations

from pathlib import Path
from typing import Any

from gaiden.application.ingest.markitdown_preprod_service import run_markitdown_preprod


def convert_source_to_markdown(
    book_code: str,
    lang: str,
    source_path: str | Path,
    force: bool = False,
) -> dict[str, Any]:
    return run_markitdown_preprod(
        book_code=book_code,
        lang=lang,
        source_path=source_path,
        promote=True,
        force=force,
    )
