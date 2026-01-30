#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path("/home/periclesguara/Projetos/gaiden_bookmaker")

CHAPTER_RE = re.compile(
    r"^\s*(\d+)\.\s+([A-ZÁÉÍÓÚÑÜ0-9 ,;:¡!¿?\-—()\"'*]+)\s*$"
)
ROMAN_RE = re.compile(r"^\s*([IVXLCDM]+)\.\s+(.+)$")
DECORATIVE_RE = re.compile(r"^\s*[-_*=]{3,}\s*$")


@dataclass
class Paths:
    merge_refine_txt: Path
    miolo_md: Path
    frontmatter_dir: Path
    book_build_md: Path
    epub_css: Path
    cover_image: Path
    epub_out: Path


def _find_cover() -> Path:
    candidates = [
        BASE_DIR
        / "data"
        / "builds"
        / "book01_the_adventures_of_sherlock_holmes"
        / "es"
        / "Cover_Sherlock_Holmes_ES.jpg",
        BASE_DIR
        / "data"
        / "builds"
        / "book01_the_adventures_of_sherlock_holmes"
        / "es"
        / "Cover_Sherlock_Holmes_ES.png",
        BASE_DIR
        / "data"
        / "builds"
        / "book_0001"
        / "es"
        / "Cover_Sherlock_Holmes_ES.jpg",
        BASE_DIR
        / "data"
        / "builds"
        / "book_0001"
        / "es"
        / "Cover_Sherlock_Holmes_ES.png",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError("Cover_Sherlock_Holmes_ES.* not found")


def _paths() -> Paths:
    base = BASE_DIR / "data" / "builds" / "book_0001" / "es"
    base.mkdir(parents=True, exist_ok=True)
    return Paths(
        merge_refine_txt=base / "merge_refine.txt",
        miolo_md=base / "MIOL_ES.from_refine.md",
        frontmatter_dir=BASE_DIR / "data" / "frontmatter" / "book_0001" / "es",
        book_build_md=base / "BOOK.BUILD.MD",
        epub_css=base / "BOOK.epub.css",
        cover_image=_find_cover(),
        epub_out=base / "BOOK.epub",
    )


def _collapse_blank_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    blank_run = 0
    for line in lines:
        if line.strip() == "":
            blank_run += 1
            if blank_run <= 2:
                out.append("")
            continue
        blank_run = 0
        out.append(line)
    return out


def convert_txt_to_md(src: Path, dst: Path) -> None:
    raw_lines = src.read_text(encoding="utf-8").splitlines()
    out_lines: list[str] = []
    for line in raw_lines:
        if DECORATIVE_RE.match(line):
            continue
        m = CHAPTER_RE.match(line)
        if m:
            chapter_title = f"## {m.group(1)}. {m.group(2)}"
            out_lines.append(chapter_title)
            out_lines.append("::: pagebreak")
            out_lines.append(":::")
            continue
        m = ROMAN_RE.match(line)
        if m:
            chapter_title = f"## {m.group(1)}. {m.group(2).strip()}"
            out_lines.append(chapter_title)
            out_lines.append("::: pagebreak")
            out_lines.append(":::")
            continue
        out_lines.append(line)

    out_lines = _collapse_blank_lines(out_lines)
    dst.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def _read_frontmatter_file(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    return text


def _ensure_pagebreak(text: str) -> str:
    if not text:
        return ""
    tail = text.splitlines()[-5:]
    if "::: pagebreak" in tail:
        return text
    return text + "\n\n::: pagebreak\n:::"


def build_book_md(frontmatter_dir: Path, miolo_md: Path, out_path: Path) -> None:
    order = [
        "frontispiece.md",
        "copyright.md",
        "about_edition.md",
        "introduction.md",
        miolo_md.name,
        "epilogue.md",
        "about_contributor.md",
    ]

    parts: list[str] = []
    for name in order:
        if name == miolo_md.name:
            miolo_text = miolo_md.read_text(encoding="utf-8").strip()
            if miolo_text:
                parts.append(miolo_text)
            continue
        text = _read_frontmatter_file(frontmatter_dir / name)
        if text:
            parts.append(_ensure_pagebreak(text))

    out_path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")


def ensure_css(css_path: Path) -> None:
    rule = "h1, h2, h3 { text-align: center; }"
    if css_path.exists():
        css = css_path.read_text(encoding="utf-8")
        if rule in css:
            return
        css = css.rstrip() + "\n" + rule + "\n"
        css_path.write_text(css, encoding="utf-8")
        return
    css_path.write_text(rule + "\n", encoding="utf-8")


def build_epub(build_md: Path, css_path: Path, cover_path: Path, out_path: Path) -> None:
    cmd = (
        "pandoc",
        str(build_md),
        "--toc",
        "--toc-depth=2",
        "--metadata",
        'title=Las Aventuras de Sherlock Holmes (Edición en Español Moderno)',
        "--metadata",
        "author=Arthur Conan Doyle",
        "--metadata",
        "lang=es",
        "--metadata",
        "publisher=RinoBooks",
        "--metadata",
        "date=2026",
        f"--css={css_path}",
        f"--epub-cover-image={cover_path}",
        "-o",
        str(out_path),
    )
    import subprocess

    subprocess.run(cmd, check=True)


def main() -> None:
    paths = _paths()
    convert_txt_to_md(paths.merge_refine_txt, paths.miolo_md)
    build_book_md(paths.frontmatter_dir, paths.miolo_md, paths.book_build_md)
    ensure_css(paths.epub_css)
    build_epub(paths.book_build_md, paths.epub_css, paths.cover_image, paths.epub_out)


if __name__ == "__main__":
    main()
