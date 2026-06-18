from __future__ import annotations

from pathlib import Path


class MarkItDownAdapter:
    def convert_to_markdown(self, source_path: str | Path) -> str:
        source = Path(source_path).expanduser()
        if not source.exists():
            raise FileNotFoundError(f"Source file not found: {source}")

        try:
            from markitdown import MarkItDown
        except ImportError as exc:
            raise RuntimeError(
                "MarkItDown is not installed. Install project dependencies before running normalize."
            ) from exc

        result = MarkItDown().convert(str(source))
        text = getattr(result, "text_content", None) or getattr(result, "markdown", None)
        if text is None:
            text = str(result)
        return str(text)
