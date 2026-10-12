"""Thin Writer-origin adapter to the existing Intake renderer and EPUB gate."""
from pathlib import Path
from uuid import uuid4

from editorial import kdp_mode
from editorial.edition_renderer import EditionRenderer
from gaiden.application.builds.epubcheck_service import stream_sha256
from gaiden.application.builds.final_epub_import import import_final_epub
from gaiden.infrastructure import storage


def canonical_source(edition):
    provenance = edition.work.source_provenance or {}
    if provenance.get("origin") != "writer_canonical":
        raise ValueError("edição sem origem canônica Writer")
    source = Path(provenance.get("working_manuscript_path") or "").resolve()
    expected = provenance.get("working_manuscript_sha256") or provenance.get("source_sha256")
    if (not expected or not source.is_relative_to(storage.storage_root().resolve())
            or not source.is_file() or stream_sha256(source) != expected):
        raise ValueError("o manuscrito Writer da edição está ausente ou diverge do hash canônico")
    return source


def assemble_writer_edition(edition):
    source = canonical_source(edition)
    kdp_mode.build_frontmatter_files(edition, storage.frontmatter_dir())
    # Renderer adds frontmatter, artwork, styles and TOC around the frozen source.
    # Do not call the legacy text-transforming build_merged_kdp_source path.
    result = EditionRenderer(edition, source_path=source).render(allow_preview_cover=True)
    canonical_source(edition)
    return result


def generate_writer_epub(edition, actor="operator"):
    source = canonical_source(edition)
    renderer = EditionRenderer(edition, source_path=source)
    if not renderer.preview_is_approved():
        raise ValueError("aprove a edição antes de gerar o EPUB")
    # Rendering compares current inputs to the approved fingerprint, invalidating drift.
    renderer.render()
    if not renderer.preview_is_approved():
        raise ValueError("a edição mudou após a aprovação; revise e aprove o preview novamente")
    output = renderer.build_epub(f"BOOK.writer-{uuid4().hex}.epub", require_approval=True)
    canonical_source(edition)
    return import_final_epub(
        edition_id=edition.pk, locale=edition.language.code.replace("_", "-"),
        source_path=output, expected_sha256=stream_sha256(output),
        expected_size_bytes=output.stat().st_size, source="WRITER_EDITION_EXPORT",
        actor=actor, official_body_path=source, approved=True,
    )
