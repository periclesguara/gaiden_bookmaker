from __future__ import annotations

import zipfile
from pathlib import Path

from gaiden.application.pipeline.ingest import extract_text_from_file
from gaiden.infrastructure.source_extractors.epub_reader import EpubReader
from gaiden.infrastructure.source_extractors.html_extractor import html_to_text


class ExtractorRegistry:
    def __init__(self):
        self._extractors: dict[str, object] = {}

    def register(self, extensions, extractor):
        for extension in extensions:
            key = extension.lower()
            self._extractors[key if key.startswith(".") else f".{key}"] = extractor

    def get_extractor(self, extension: str, mime_type: str = ""):
        key = extension.lower()
        key = key if key.startswith(".") else f".{key}"
        return self._extractors.get(key)


class PipelineExtractor:
    def __init__(self, extension: str | None = None):
        self.extension = extension

    def extract(self, path: Path) -> str | None:
        extension = self.extension or path.suffix.lstrip(".")
        if extension in {"markdown"}:
            extension = "md"
        if extension in {"xhtml", "xml"}:
            raw = path.read_text(encoding="utf-8", errors="replace")
            return html_to_text(raw)
        return extract_text_from_file(path, extension)


class EpubTextExtractor:
    def extract(self, path: Path) -> str | None:
        package = EpubReader(path).read()
        parts: list[str] = []
        with zipfile.ZipFile(path) as archive:
            for item in package.spine:
                if item.get("media_type") not in {"application/xhtml+xml", "text/html"}:
                    continue
                raw = archive.read(item["path"]).decode("utf-8", errors="replace")
                text = html_to_text(raw)
                if text:
                    parts.append(text)
        return "\n\n".join(parts).strip() or None


def default_registry() -> ExtractorRegistry:
    registry = ExtractorRegistry()
    registry.register({".txt", ".md", ".markdown"}, PipelineExtractor("txt"))
    registry.register({".html", ".htm"}, PipelineExtractor("html"))
    registry.register({".xhtml", ".xml"}, PipelineExtractor("xhtml"))
    registry.register({".pdf"}, PipelineExtractor("pdf"))
    registry.register({".docx"}, PipelineExtractor("docx"))
    registry.register({".epub"}, EpubTextExtractor())
    return registry
