#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path("/home/periclesguara/Projetos/gaiden_bookmaker")


@dataclass
class EditionPaths:
    base: Path
    miolo_md: Path
    final_md: Path
    build_md: Path
    frontmatter_dir: Path

    @classmethod
    def for_sherlock_es(cls) -> "EditionPaths":
        base = (
            BASE_DIR
            / "data"
            / "builds"
            / "book01_the_adventures_of_sherlock_holmes"
            / "es"
        )
        return cls(
            base=base,
            miolo_md=base / "MIOL_TERM.v1.md",
            final_md=base / "BOOK.MD_FINAL",
            build_md=base / "BOOK.BUILD.MD",
            frontmatter_dir=BASE_DIR
            / "data"
            / "frontmatter"
            / "book01_the_adventures_of_sherlock_holmes"
            / "es",
        )


def _strip_leading_title(text: str, title: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip().lower() == title.strip().lower():
        return "\n".join(lines[1:]).strip()
    return text.strip()


def _read_frontmatter(frontmatter_dir: Path) -> tuple[str, str, str, str]:
    title_path = frontmatter_dir / "title_page.md"
    if not title_path.exists():
        title_path = frontmatter_dir / "frontispiece.md"
    title_page = title_path.read_text(encoding="utf-8")
    copyright_text = (frontmatter_dir / "copyright.md").read_text(
        encoding="utf-8"
    )
    about = (frontmatter_dir / "about_edition.md").read_text(encoding="utf-8")
    source_path = frontmatter_dir / "source_record.md"
    source_record = source_path.read_text(encoding="utf-8") if source_path.exists() else ""

    title_page = _strip_leading_title(title_page, "Title Page")
    copyright_text = _strip_leading_title(copyright_text, "Derechos de autor")
    about = _strip_leading_title(about, "Sobre esta edición")

    return title_page, copyright_text, source_record, about


def build() -> None:
    paths = EditionPaths.for_sherlock_es()

    title_page, copyright_text, source_record, about = _read_frontmatter(
        paths.frontmatter_dir
    )
    miolo_md = paths.miolo_md.read_text(encoding="utf-8").strip()

    parts = [
        "# Title Page {.frontmatter-title .unlisted}\n\n" + title_page,
        "# Derechos de autor {.frontmatter-title .unlisted}\n\n" + copyright_text,
        source_record,
        "# Sobre esta edición {.frontmatter-title .unlisted}\n\n" + about,
        miolo_md,
    ]
    final_text = "\n\n".join(p for p in parts if p.strip()) + "\n"

    paths.final_md.write_text(final_text, encoding="utf-8")
    paths.build_md.write_text(final_text, encoding="utf-8")


if __name__ == "__main__":
    build()
