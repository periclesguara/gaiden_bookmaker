from __future__ import annotations

import re
import subprocess
from pathlib import Path

from editorial.frontmatter import build_frontmatter_files
from editorial.models import Edition
from pipeline.services import inserts, paths as ppaths


def builds_dir(edition: Edition) -> Path:
    builds_base = ppaths.edition_build_dir(edition)
    assert "web/data" not in str(builds_base), "BUG: builds_dir apontando para web/data"
    return builds_base


def frontmatter_dir(edition: Edition) -> Path:
    fm = ppaths.data_dir() / "frontmatter" / edition.work.code / edition.language.code
    assert "/web/" not in str(fm), "BUG: frontmatter_dir apontando para web/"
    return fm


def translated_miolo_path(edition: Edition) -> Path:
    mp = ppaths.miolo_md_path(edition)
    assert "/web/" not in str(mp), "BUG: miolo_path apontando para web/"
    return mp


_PAGEBREAK_RE = re.compile(r"^:::\s*pagebreak\s*$", re.MULTILINE)
_TRAILING_PAGEBREAK_RE = re.compile(r"(?:\n*:::\s*pagebreak\s*:::\s*)+$", re.MULTILINE)
_EMPTY_SECTION_RE = re.compile(r"^\s*(?:<!--.*?-->\s*)*$", re.DOTALL)
_CHAPTER_TITLE_RE = re.compile(r"^#\s+.*\{\.chapter-title\b.*\}$", re.MULTILINE)
_GENERIC_CHAPTER_RE = re.compile(
    r"^#\s+(CAP[IÍ]TULO|CAPITULO|CHAPTER|KAPITEL|CAPITOL)\b",
    re.MULTILINE,
)


def _resolve_cover_path(edition: Edition) -> Path | None:
    cover_value = (getattr(edition, "cover_filepath", "") or "").strip()
    project_root = Path(__file__).resolve().parents[2]
    if cover_value:
        cover_path = Path(cover_value)
        if not cover_path.is_absolute():
            cover_path = project_root / cover_path
        if cover_path.exists():
            return cover_path

    cover_dir = project_root / "data" / "covers" / edition.work.code / edition.language.code
    for name in ("cover.jpg", "cover.png"):
        candidate = cover_dir / name
        if candidate.exists():
            return candidate
    return None


def _normalize_pagebreaks(text: str) -> str:
    return _PAGEBREAK_RE.sub("::: pagebreak\n:::", text)

def _strip_trailing_pagebreaks(text: str) -> str:
    return _TRAILING_PAGEBREAK_RE.sub("", text).rstrip()

def _is_effectively_empty(text: str) -> bool:
    return _EMPTY_SECTION_RE.match(text) is not None

def _strip_canonical_frontmatter(text: str) -> str:
    for regex in (_CHAPTER_TITLE_RE, _GENERIC_CHAPTER_RE):
        match = regex.search(text)
        if match:
            return text[match.start():].lstrip()
    return text.strip()


def build_merged_kdp_source(edition: Edition, version_override: str | None = None) -> Path:
    fm_base = frontmatter_dir(edition)
    builds_base = builds_dir(edition)
    builds_base.mkdir(parents=True, exist_ok=True)
    inserts_path = builds_base / "inserts.json"
    spec = inserts.load_inserts_json(inserts_path)
    post_cover_block = ""
    if spec:
        inserts.prepare_build_images(builds_base, spec)
        post_cover_block = inserts.build_post_cover_blocks(spec)

    sections: list[str] = []
    for name in [
        "frontispiece",
        "copyright",
        "about_edition",
        "introduction",
        "epilogue",
        "about_contributor",
    ]:
        path = fm_base / f"{name}.md"
        if path.exists():
            txt = _strip_trailing_pagebreaks(
                _normalize_pagebreaks(path.read_text(encoding="utf-8").rstrip())
            )
            if txt and not _is_effectively_empty(txt):
                sections.append(txt)
    if post_cover_block:
        sections.insert(0, post_cover_block)

    canonical_ready = ppaths.canonical_ready_md_path(edition)
    if not canonical_ready.exists():
        legacy_ready = (
            ppaths.data_dir()
            / "canonical"
            / edition.work.code
            / edition.language.code
            / "BOOK.MD_FINAL.ready.md"
        )
        if legacy_ready.exists():
            canonical_ready = legacy_ready
    miolo_path = canonical_ready if canonical_ready.exists() else translated_miolo_path(edition)
    if not miolo_path.exists():
        raise FileNotFoundError(f"Miolo traduzido nao encontrado: {miolo_path}")

    miolo_txt = miolo_path.read_text(encoding="utf-8").strip()
    if canonical_ready.exists():
        miolo_txt = _strip_canonical_frontmatter(miolo_txt)
    if spec:
        miolo_txt = inserts.inject_images_into_miolo_md(miolo_txt, spec).strip()
        inserts.validate_illustrated_miolo(miolo_txt, spec)
    pagebreak = "\n\n::: pagebreak\n:::\n\n"
    merged_txt = pagebreak.join(sections).rstrip()
    if merged_txt:
        merged_txt = f"{merged_txt}{pagebreak}{miolo_txt}\n"
    else:
        merged_txt = f"{miolo_txt}\n"

    kdp_merged_path = ppaths.kdp_merged_md_path(edition, version=version_override)
    book_build_path = ppaths.build_md_path(edition, version=version_override)

    kdp_merged_path.write_text(merged_txt, encoding="utf-8")
    book_build_path.write_text(merged_txt, encoding="utf-8")

    return kdp_merged_path


def build_epub_for_edition(
    edition: Edition,
    epub_filename: str = "ebook.epub",
    version_override: str | None = None,
) -> Path:
    builds_base = builds_dir(edition)
    builds_base.mkdir(parents=True, exist_ok=True)

    merged_path = ppaths.kdp_merged_md_path(edition, version=version_override)
    if not merged_path.exists():
        raise FileNotFoundError(f"Arquivo de merge nao encontrado: {merged_path}")

    epub_path = builds_base / epub_filename

    title = (edition.title or "").strip() or "Die Abenteuer des Sherlock Holmes"
    lang = edition.language.code
    subtitle = (getattr(edition, "subtitle", "") or "").strip()

    inserts_path = builds_base / "inserts.json"
    spec = inserts.load_inserts_json(inserts_path)
    split_level = "1"

    cmd = [
        "pandoc",
        str(merged_path),
        "--from=markdown+markdown_in_html_blocks",
        "--toc",
        "--toc-depth=2",
        "--epub-chapter-level=1",
        f"--split-level={split_level}",
        f"--metadata=title:{title}",
        f"--metadata=lang:{lang}",
        f"--metadata=language:{lang}",
    ]
    cover_path = _resolve_cover_path(edition)
    if cover_path:
        cmd.append(f"--epub-cover-image={cover_path}")
    if subtitle:
        cmd.append(f"--metadata=subtitle:{subtitle}")
    cmd += ["-o", str(epub_path)]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "Erro ao rodar Pandoc para "
            f"{edition.work.code} [{edition.language.code}]:\n"
            f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        )

    if spec:
        inserts.rewrite_epub_illustrated_images(epub_path, spec, builds_base)
        inserts.validate_epub_images(epub_path, spec)

    return epub_path


def build_kdp_for_edition(edition: Edition, version_override: str | None = None) -> dict:
    build_frontmatter_files(edition, ppaths.data_dir() / "frontmatter")
    merged_path = build_merged_kdp_source(edition, version_override=version_override)
    epub_path = build_epub_for_edition(edition, version_override=version_override)

    return {
        "frontmatter_dir": frontmatter_dir(edition),
        "merged": merged_path,
        "book_build": ppaths.build_md_path(edition, version=version_override),
        "epub": epub_path,
    }


def build_print_pdf_for_edition(
    edition: Edition,
    variant: str = "print",
    version_override: str | None = None,
) -> Path:
    builds_base = builds_dir(edition)
    builds_base.mkdir(parents=True, exist_ok=True)

    merged_path = ppaths.kdp_merged_md_path(edition, version=version_override)
    if not merged_path.exists():
        raise FileNotFoundError(f"Arquivo de merge nao encontrado: {merged_path}")

    pdf_path = builds_base / "BOOK.PRINT.PDF"
    cmd = [
        "pandoc",
        str(merged_path),
        "--from=markdown+markdown_in_html_blocks",
        "-V",
        "geometry:margin=2cm",
        "-V",
        "papersize:6x9in",
        "-o",
        str(pdf_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "Erro ao gerar PRINT PDF para "
            f"{edition.work.code} [{edition.language.code}]:\n"
            f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        )

    return pdf_path


def run_epubcheck_for_edition(edition: Edition, epubcheck_cmd: str = "epubcheck") -> Path:
    builds_base = builds_dir(edition)
    epub_path = builds_base / "BOOK.EPUB3"
    if not epub_path.exists():
        alt = builds_base / "ebook.epub"
        if alt.exists():
            epub_path = alt
        else:
            raise FileNotFoundError(f"Nenhum EPUB encontrado em {builds_base}")

    cmd = [epubcheck_cmd, str(epub_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "epubcheck encontrou problemas no EPUB para "
            f"{edition.work.code} [{edition.language.code}]:\n"
            f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        )
    return epub_path


def gaiden_build_full_book(edition: Edition, version_override: str | None = None) -> dict:
    build_frontmatter_files(edition, ppaths.data_dir() / "frontmatter")
    merged_path = build_merged_kdp_source(edition, version_override=version_override)
    book_build_path = ppaths.build_md_path(edition, version=version_override)

    epub_path = build_epub_for_edition(edition, version_override=version_override)
    book_epub3 = builds_dir(edition) / "BOOK.EPUB3"
    if not book_epub3.exists():
        book_epub3.write_bytes(epub_path.read_bytes())
        epub_path = book_epub3

    pdf_path = build_print_pdf_for_edition(edition, variant="print", version_override=version_override)

    return {
        "frontmatter_dir": frontmatter_dir(edition),
        "merged": merged_path,
        "book_build": book_build_path,
        "epub": epub_path,
        "pdf": pdf_path,
    }

def run_txt_to_miolo_from_reference(edition):
    """
    Bridge: centraliza a geração do miolo a partir do TXT referência (locks).
    Mantém API usada pela UI/commands.
    """
    from pipeline.services.miolo_transform import run_txt_to_miolo_from_reference as _impl
    return _impl(edition)
