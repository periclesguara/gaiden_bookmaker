from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import edition_meta, paths, text_source


@dataclass
class ManifestTextSource:
    canonical_name: Optional[str]
    canonical_path: Optional[str]
    pipeline_step: Optional[str]
    pipeline_job_id: Optional[int]
    pipeline_filepath: Optional[str]


@dataclass
class ManifestMdFiles:
    pre_qa: Optional[str]
    qa: Optional[str]
    final: Optional[str]


@dataclass
class ManifestBuildInfo:
    path: Optional[str]
    title_page_template: Optional[str]
    frontispiece_template: Optional[str]
    copyright_template: Optional[str]
    about_edition_template: Optional[str]
    about_contributor_template: Optional[str]


@dataclass
class ManifestExportInfo:
    epub: Optional[str]
    pdf: Optional[str]
    epubcheck_status: Optional[str]


@dataclass
class BookManifest:
    edition_id: int
    book_code: str
    language: str
    edition_type: Optional[str]
    imprint_name: Optional[str]
    collection_name: Optional[str]
    text_source: ManifestTextSource
    md_files: ManifestMdFiles
    build: ManifestBuildInfo
    export: ManifestExportInfo
    export_date: str
    export_user: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_path(path: Path) -> Optional[str]:
    return str(path) if path.exists() else None


def build_manifest(
    edition,
    target_edition=None,
    *,
    export_user: str = "system",
    epubcheck_status: str = "unknown",
) -> BookManifest:
    effective = target_edition or edition
    ts = text_source.get_effective_text_source(effective)

    text_source_info = ManifestTextSource(
        canonical_name=ts.canonical_name,
        canonical_path=str(ts.canonical_path) if ts.canonical_path else None,
        pipeline_step=ts.job_stage,
        pipeline_job_id=ts.job_id,
        pipeline_filepath=ts.job_filepath,
    )

    md_files = ManifestMdFiles(
        pre_qa=_safe_path(paths.pre_qa_md_path(effective)),
        qa=_safe_path(paths.qa_md_path(effective)),
        final=_safe_path(paths.final_md_path(effective)),
    )

    build_info = ManifestBuildInfo(
        path=_safe_path(paths.build_md_path(effective)),
        title_page_template="title_page.md.j2",
        frontispiece_template="frontispiece.md.j2",
        copyright_template="copyright.md.j2",
        about_edition_template="about_edition.md.j2",
        about_contributor_template="about_contributor.md.j2",
    )

    export_info = ManifestExportInfo(
        epub=_safe_path(paths.epub_path(effective)),
        pdf=_safe_path(paths.pdf_path(effective)),
        epubcheck_status=epubcheck_status,
    )

    export_date = datetime.now(timezone.utc).isoformat(timespec="seconds")

    return BookManifest(
        edition_id=effective.id,
        book_code=edition_meta.book_code(effective),
        language=edition_meta.language_code(effective),
        edition_type=getattr(effective, "edition_type", None),
        imprint_name=getattr(effective, "imprint_name", None),
        collection_name=getattr(effective, "collection_name", None),
        text_source=text_source_info,
        md_files=md_files,
        build=build_info,
        export=export_info,
        export_date=export_date,
        export_user=export_user,
    )


def manifest_path(edition) -> Path:
    return paths.edition_build_dir(edition) / "BOOK.MANIFEST.json"


def write_manifest(edition, manifest: BookManifest) -> Path:
    path = manifest_path(edition)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    return path
