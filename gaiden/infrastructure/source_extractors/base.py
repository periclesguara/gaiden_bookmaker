from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from gaiden.infrastructure import storage

SCHEMA = "source_extract_v1"


@dataclass(frozen=True)
class SourceExtractPaths:
    book_code: str
    lang: str
    original_ext: str
    raw_dir: Path
    images_dir: Path
    original_file: Path
    canonical_txt: Path
    canonical_html: Path
    meta_file: Path


class SourceExtractor(Protocol):
    input_format: str

    def extract(self, original_file: Path, *, book_code: str, lang: str) -> dict:
        ...


def canonical_paths(book_code: str, lang: str, original_ext: str) -> SourceExtractPaths:
    ext = original_ext if original_ext.startswith(".") else f".{original_ext}"
    raw_dir = storage.raw_dir(book_code) / lang
    images_dir = storage.images_dir(book_code, lang)
    return SourceExtractPaths(
        book_code=book_code,
        lang=lang,
        original_ext=ext,
        raw_dir=raw_dir,
        images_dir=images_dir,
        original_file=raw_dir / f"source{ext}",
        canonical_txt=raw_dir / "source.txt",
        canonical_html=raw_dir / "source.html",
        meta_file=raw_dir / "source_meta.json",
    )


def ensure_canonical_dirs(paths: SourceExtractPaths) -> None:
    paths.raw_dir.mkdir(parents=True, exist_ok=True)
    paths.images_dir.mkdir(parents=True, exist_ok=True)


def default_details(**overrides) -> dict:
    details = {
        "title": "",
        "creators": [],
        "languages": [],
        "publisher": "",
        "rights": "",
        "spine_count": 0,
        "toc_count": 0,
        "images_count": 0,
    }
    details.update(overrides)
    return details


def repo_display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(storage.repo_root()))
    except ValueError:
        return str(path)


def normalize_result(
    *,
    input_format: str,
    paths: SourceExtractPaths,
    warnings: list[str] | None = None,
    details: dict | None = None,
) -> dict:
    payload = {
        "schema": SCHEMA,
        "status": "ok",
        "input_format": input_format,
        "original_file": repo_display_path(paths.original_file),
        "canonical_txt": repo_display_path(paths.canonical_txt),
        "canonical_html": repo_display_path(paths.canonical_html),
        "images_dir": repo_display_path(paths.images_dir),
        "meta_file": repo_display_path(paths.meta_file),
        "warnings": warnings or [],
        "details": default_details(**(details or {})),
    }
    return payload


def write_source_meta(result: dict, paths: SourceExtractPaths) -> None:
    paths.meta_file.parent.mkdir(parents=True, exist_ok=True)
    paths.meta_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
