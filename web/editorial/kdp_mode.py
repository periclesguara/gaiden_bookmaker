from __future__ import annotations

import re
import subprocess
from pathlib import Path

from editorial.frontmatter import build_frontmatter_files
from editorial.models import Edition


def builds_dir(edition: Edition) -> Path:
    return Path("data") / "builds" / edition.work.code / edition.language.code


def frontmatter_dir(edition: Edition) -> Path:
    return Path("data") / "frontmatter" / edition.work.code / edition.language.code


def translated_miolo_path(edition: Edition) -> Path:
    return Path("data") / "translated" / edition.work.code / edition.language.code / "miolo.md"


_PAGEBREAK_RE = re.compile(r"^:::\s*pagebreak\s*$", re.MULTILINE)


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


def build_merged_kdp_source(edition: Edition) -> Path:
    fm_base = frontmatter_dir(edition)
    builds_base = builds_dir(edition)
    builds_base.mkdir(parents=True, exist_ok=True)

    sections: list[str] = []
    for name in ["frontispiece", "copyright", "about_edition", "about_contributor"]:
        path = fm_base / f"{name}.md"
        if path.exists():
            txt = _normalize_pagebreaks(path.read_text(encoding="utf-8").rstrip())
            sections.append(txt + "\n\n")

    miolo_path = translated_miolo_path(edition)
    if not miolo_path.exists():
        raise FileNotFoundError(f"Miolo traduzido nao encontrado: {miolo_path}")

    miolo_txt = miolo_path.read_text(encoding="utf-8").strip()
    merged_txt = "".join(sections) + "\n\n" + miolo_txt + "\n"

    kdp_merged_path = builds_base / "kdp_merged.md"
    book_build_path = builds_base / "BOOK.BUILD.MD"

    kdp_merged_path.write_text(merged_txt, encoding="utf-8")
    book_build_path.write_text(merged_txt, encoding="utf-8")

    return kdp_merged_path


def build_epub_for_edition(edition: Edition, epub_filename: str = "ebook.epub") -> Path:
    builds_base = builds_dir(edition)
    builds_base.mkdir(parents=True, exist_ok=True)

    merged_path = builds_base / "kdp_merged.md"
    if not merged_path.exists():
        raise FileNotFoundError(f"Arquivo de merge nao encontrado: {merged_path}")

    epub_path = builds_base / epub_filename

    title = (edition.title or "").strip() or "Die Abenteuer des Sherlock Holmes"
    lang = edition.language.code
    subtitle = (getattr(edition, "subtitle", "") or "").strip()

    cmd = [
        "pandoc",
        str(merged_path),
        "--toc",
        "--toc-depth=2",
        "--epub-chapter-level=1",
        "--split-level=1",
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

    return epub_path


def build_kdp_for_edition(edition: Edition) -> dict:
    build_frontmatter_files(edition, Path("data") / "frontmatter")
    merged_path = build_merged_kdp_source(edition)
    epub_path = build_epub_for_edition(edition)

    return {
        "frontmatter_dir": frontmatter_dir(edition),
        "merged": merged_path,
        "book_build": builds_dir(edition) / "BOOK.BUILD.MD",
        "epub": epub_path,
    }


def build_print_pdf_for_edition(edition: Edition, variant: str = "print") -> Path:
    builds_base = builds_dir(edition)
    builds_base.mkdir(parents=True, exist_ok=True)

    merged_path = builds_base / "kdp_merged.md"
    if not merged_path.exists():
        raise FileNotFoundError(f"Arquivo de merge nao encontrado: {merged_path}")

    pdf_path = builds_base / "BOOK.PRINT.PDF"
    cmd = [
        "pandoc",
        str(merged_path),
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


def gaiden_build_full_book(edition: Edition) -> dict:
    build_frontmatter_files(edition, Path("data") / "frontmatter")
    merged_path = build_merged_kdp_source(edition)
    book_build_path = builds_dir(edition) / "BOOK.BUILD.MD"

    epub_path = build_epub_for_edition(edition)
    book_epub3 = builds_dir(edition) / "BOOK.EPUB3"
    if not book_epub3.exists():
        book_epub3.write_bytes(epub_path.read_bytes())
        epub_path = book_epub3

    pdf_path = build_print_pdf_for_edition(edition, variant="print")

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
