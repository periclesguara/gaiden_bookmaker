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
    def for_sherlock_ptbr(cls) -> "EditionPaths":
        base = (
            BASE_DIR
            / "data"
            / "builds"
            / "book01_the_adventures_of_sherlock_holmes"
            / "ptbr"
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
            / "ptbr",
        )


def _strip_leading_title(text: str, title: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip().lower() == title.strip().lower():
        return "\n".join(lines[1:]).strip()
    return text.strip()


def _read_frontmatter(frontmatter_dir: Path) -> tuple[str, str, str]:
    frontispiece = (frontmatter_dir / "frontispiece.md").read_text(
        encoding="utf-8"
    )
    copyright_text = (frontmatter_dir / "copyright.md").read_text(
        encoding="utf-8"
    )
    about = (frontmatter_dir / "about_edition.md").read_text(encoding="utf-8")

    frontispiece = _strip_leading_title(frontispiece, "Frontispício")
    copyright_text = _strip_leading_title(copyright_text, "Direitos autorais")
    about = _strip_leading_title(about, "Sobre essa edição")

    return frontispiece, copyright_text, about


def build() -> None:
    paths = EditionPaths.for_sherlock_ptbr()

    frontispiece, copyright_text, about = _read_frontmatter(
        paths.frontmatter_dir
    )
    miolo_md = paths.miolo_md.read_text(encoding="utf-8").strip()

    parts = [
        "# Frontispício {.frontmatter-title .unlisted}\n\n" + frontispiece,
        "# Direitos autorais {.frontmatter-title .unlisted}\n\n" + copyright_text,
        "# Sobre essa edição {.frontmatter-title .unlisted}\n\n" + about,
        miolo_md,
    ]
    final_text = "\n\n".join(p for p in parts if p.strip()) + "\n"

    paths.final_md.write_text(final_text, encoding="utf-8")
    paths.build_md.write_text(final_text, encoding="utf-8")


if __name__ == "__main__":
    build()
