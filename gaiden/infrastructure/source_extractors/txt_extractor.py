from __future__ import annotations

from html import escape
from pathlib import Path

from .base import canonical_paths, ensure_canonical_dirs, normalize_result, write_source_meta


class TxtExtractor:
    input_format = "txt"

    def extract(self, original_file: Path, *, book_code: str, lang: str) -> dict:
        paths = canonical_paths(book_code, lang, ".txt")
        ensure_canonical_dirs(paths)

        text = original_file.read_text(encoding="utf-8", errors="replace")
        if original_file.resolve() != paths.canonical_txt.resolve():
            paths.canonical_txt.write_text(text, encoding="utf-8")
        else:
            paths.canonical_txt.write_text(text, encoding="utf-8")

        html = (
            '<!doctype html>\n'
            '<html><head><meta charset="utf-8"></head><body><pre>'
            f"{escape(text)}"
            "</pre></body></html>\n"
        )
        paths.canonical_html.write_text(html, encoding="utf-8")

        result = normalize_result(input_format=self.input_format, paths=paths)
        write_source_meta(result, paths)
        return result
