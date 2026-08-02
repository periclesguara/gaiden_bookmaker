from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ArtifactReference:
    path: str
    sha256: str
    size: int | None = None
    status: str = ""


@dataclass(frozen=True)
class EditorialEditionPackage:
    language: str
    locale: str
    metadata: dict[str, Any]
    frontmatter: dict[str, Any]
    body: ArtifactReference | None = None
    validation: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EditorialImportPackage:
    schema_version: int
    package_type: str
    book: dict[str, Any]
    source: ArtifactReference
    source_intake_item_id: int | None
    editions: tuple[EditorialEditionPackage, ...]
    status: str
    incremental: dict[str, Any]
    raw: dict[str, Any]

    @property
    def book_code(self) -> str:
        return str(self.book["book_code"])


@dataclass(frozen=True)
class ValidatedEditorialPackage:
    package: EditorialImportPackage
    package_path: Path
    package_sha256: str
    artifact_root: Path
    artifact_paths: dict[str, Path]
    warnings: tuple[str, ...] = ()


PIPELINE_LANGUAGE_BY_LOCALE = {
    "en": "en",
    "en-GB": "en",
    "en-US": "en",
    "pt-BR": "ptbr",
    "ptbr": "ptbr",
    "pt-br": "ptbr",
    "es": "es",
    "de": "de",
    "fr": "fr",
    "it": "it",
}


def pipeline_language(language: str, locale: str = "") -> str:
    return PIPELINE_LANGUAGE_BY_LOCALE.get(locale) or PIPELINE_LANGUAGE_BY_LOCALE.get(language, "")
