from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from django.core.exceptions import ObjectDoesNotExist
from django.utils.text import slugify

from editorial.services.metadata import price_cents, rights_statement
from editorial.storefront_availability import derive_storefront_availability, normalize_sales_channels
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
    edition_code: Optional[str]
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
    status: str
    contract_version: int
    storefront: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_path(path: Path) -> Optional[str]:
    return str(path) if path.exists() else None


def _safe_epub_path(edition) -> Optional[str]:
    build_dir = paths.edition_build_dir(edition)
    candidates = [
        paths.epub_path(edition),
        build_dir / "BOOK.EPUB3",
        build_dir / "ebook.epub",
    ]
    return next((str(path) for path in candidates if path.exists()), None)


def _edition_metadata(edition):
    try:
        return edition.metadata
    except ObjectDoesNotExist:
        return None


def _legacy_storefront(edition, *, ebook_attached: bool) -> dict[str, Any]:
    title = (getattr(edition, "title", "") or "").strip() or edition.work.title
    author = (getattr(edition, "author", "") or "").strip() or edition.work.author.name
    author_parts = author.split(maxsplit=1)
    availability_status = "COMING_SOON" if ebook_attached else "NOT_ATTACHED"
    availability_label = (
        "Lançamento em breve" if ebook_attached else "E-book não anexado"
    )
    return {
        "slug": slugify(f"{title}-{edition_meta.language_code(edition)}"),
        "title": title,
        "subtitle": (getattr(edition, "subtitle", "") or "").strip(),
        "original_title": edition.work.title,
        "author": {
            "first_name": author_parts[0] if author_parts else "",
            "last_name": author_parts[1] if len(author_parts) > 1 else "",
            "pseudonym": "",
        },
        "description": (getattr(edition, "about_edition_text", "") or "").strip(),
        "short_description": "",
        "seo_title": "",
        "seo_description": "",
        "keywords": [],
        "primary_category": "",
        "subcategory": "",
        "theme": "",
        "target_audience": "",
        "cover_alt": "",
        "isbn": (getattr(edition, "isbn", "") or "").strip(),
        "rights_statement": (getattr(edition, "copyright_text", "") or "").strip(),
        "price_cents": getattr(edition, "price_cents", None),
        "currency": (getattr(edition, "currency", "") or "BRL").strip().upper(),
        "hotmart_url": "",
        "lulu_url": "",
        "sales_channels": [],
        "availability": {
            "status": availability_status,
            "label": availability_label,
            "ebook_attached": ebook_attached,
            "active_sales_channels": [],
        },
    }


def _canonical_storefront(metadata, *, ebook_attached: bool) -> dict[str, Any]:
    channels = normalize_sales_channels(metadata)
    availability = derive_storefront_availability(
        metadata,
        ebook_attached=ebook_attached,
    )
    return {
        "slug": metadata.slug or "",
        "title": metadata.commercial_title,
        "subtitle": metadata.subtitle,
        "original_title": metadata.original_title,
        "author": {
            "first_name": metadata.author_first_name,
            "last_name": metadata.author_last_name,
            "pseudonym": metadata.author_pseudonym,
        },
        "description": metadata.description,
        "short_description": metadata.short_description,
        "seo_title": metadata.seo_title,
        "seo_description": metadata.seo_description,
        "keywords": list(metadata.keywords or []),
        "primary_category": metadata.primary_category,
        "subcategory": metadata.subcategory,
        "theme": metadata.theme,
        "target_audience": metadata.target_audience,
        "cover_alt": metadata.cover_alt,
        "isbn": metadata.isbn,
        "rights_statement": rights_statement(metadata),
        "price_cents": price_cents(metadata),
        "currency": metadata.currency,
        "hotmart_url": metadata.hotmart_url,
        "lulu_url": metadata.lulu_url,
        "sales_channels": channels,
        "availability": {
            "status": availability.status,
            "label": availability.label,
            "ebook_attached": availability.ebook_attached,
            "active_sales_channels": list(availability.active_sales_channels),
        },
        "edition_number": metadata.edition_number,
        "publication_year": metadata.publication_year,
        "original_language": metadata.original_language,
        "release_date": (
            metadata.expected_release_date.isoformat()
            if metadata.expected_release_date
            else ""
        ),
        "rights": {
            "work_type": metadata.work_type,
            "base_work_year": metadata.base_work_year,
            "consulted_source": metadata.consulted_source,
            "legal_basis": metadata.legal_basis,
            "edition_nature": metadata.edition_nature,
            "editorial_modifications": metadata.editorial_modifications,
            "authorized_territories": metadata.authorized_territories,
            "blocked_territories": metadata.blocked_territories,
            "evidence": metadata.rights_evidence,
        },
        "sample": {
            "title": metadata.sample_title,
            "content": metadata.sample_content,
        },
        "promotional_images": list(metadata.promotional_images or []),
    }


def build_manifest(
    edition,
    target_edition=None,
    *,
    export_user: str = "system",
    epubcheck_status: str = "unknown",
    epub_path_override: Path | None = None,
) -> BookManifest:
    effective = target_edition or edition
    metadata = _edition_metadata(effective)
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
        frontispiece_template="frontispiece.md.j2",
        copyright_template="copyright.md.j2",
        about_edition_template="about_edition.md.j2",
        about_contributor_template="about_contributor.md.j2",
    )

    export_info = ManifestExportInfo(
        epub=(
            _safe_path(Path(epub_path_override))
            if epub_path_override
            else _safe_epub_path(effective)
        ),
        pdf=_safe_path(paths.pdf_path(effective)),
        epubcheck_status=epubcheck_status,
    )
    ebook_attached = bool(export_info.epub)

    export_date = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )

    return BookManifest(
        edition_id=effective.id,
        book_code=edition_meta.book_code(effective),
        edition_code=metadata.edition_code if metadata else None,
        language=(
            metadata.regional_language
            if metadata and metadata.regional_language
            else edition_meta.language_code(effective)
        ),
        edition_type=(
            metadata.edition_format
            if metadata
            else getattr(effective, "edition_type", None)
        ),
        imprint_name=(
            metadata.imprint_name
            if metadata
            else getattr(effective, "imprint_name", None)
        ),
        collection_name=(
            metadata.collection_name
            if metadata
            else getattr(effective, "collection_name", None)
        ),
        text_source=text_source_info,
        md_files=md_files,
        build=build_info,
        export=export_info,
        export_date=export_date,
        export_user=export_user,
        status="DRAFT",
        contract_version=2,
        storefront=(
            _canonical_storefront(metadata, ebook_attached=ebook_attached)
            if metadata
            else _legacy_storefront(effective, ebook_attached=ebook_attached)
        ),
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
