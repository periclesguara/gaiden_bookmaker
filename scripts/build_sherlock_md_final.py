#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


BASE_DIR = Path("/home/periclesguara/Projetos/gaiden_bookmaker")


CHAPTER_TITLES = [
    "A Scandal in Bohemia",
    "The Red-Headed League",
    "A Case of Identity",
    "The Boscombe Valley Mystery",
    "The Five Orange Pips",
    "The Man with the Twisted Lip",
    "The Adventure of the Blue Carbuncle",
    "The Adventure of the Speckled Band",
    "The Adventure of the Engineer's Thumb",
    "The Adventure of the Noble Bachelor",
    "The Adventure of the Beryl Coronet",
    "The Adventure of the Copper Beeches",
]


@dataclass
class EditionPaths:
    base: Path
    merge_txt: Path
    miolo_md: Path
    final_md: Path
    build_md: Path
    frontmatter_dir: Path

    @classmethod
    def for_sherlock_en(cls) -> "EditionPaths":
        base = (
            BASE_DIR
            / "data"
            / "builds"
            / "book01_the_adventures_of_sherlock_holmes"
            / "en"
        )
        return cls(
            base=base,
            merge_txt=base / "merge_translate.txt",
            miolo_md=base / "MIOL_TERM.v1.md",
            final_md=base / "BOOK.MD_FINAL",
            build_md=base / "BOOK.BUILD.MD",
            frontmatter_dir=BASE_DIR
            / "data"
            / "frontmatter"
            / "book01_the_adventures_of_sherlock_holmes"
            / "en",
        )


def _load_chapters_from_merge(path: Path) -> dict[int, str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    header_re = re.compile(r"^(\d+)\.\s+(.*)$")
    chapters: dict[int, list[str]] = {}
    current_num: int | None = None

    for line in lines:
        match = header_re.match(line.strip())
        if match:
            current_num = int(match.group(1))
            chapters[current_num] = []
            continue
        if current_num is None:
            continue
        chapters[current_num].append(line)

    return {k: "\n".join(v).strip() for k, v in chapters.items()}


def _build_contents_page() -> str:
    lines = ["# Contents", ""]
    for idx, title in enumerate(CHAPTER_TITLES, start=1):
        lines.append(f"CAPÍTULO {idx} — {title}  ")
    lines.append("")
    return "\n".join(lines)


def _build_miolo_md(chapters: dict[int, str]) -> str:
    parts: list[str] = []
    for idx, title in enumerate(CHAPTER_TITLES, start=1):
        body = chapters.get(idx, "")
        parts.append(f"# CHAPTER {idx:02d} — {title.upper()} {{.chapter-title}}")
        parts.append("")
        if body:
            parts.append(body)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


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
    copyright_text = _strip_leading_title(copyright_text, "Copyright")
    about = _strip_leading_title(about, "About this edition")

    return title_page, copyright_text, source_record, about


def build() -> None:
    paths = EditionPaths.for_sherlock_en()
    chapters = _load_chapters_from_merge(paths.merge_txt)
    missing = [idx for idx in range(1, 13) if idx not in chapters]
    if missing:
        raise SystemExit(f"Missing chapters in merge_translate.txt: {missing}")

    miolo_md = _build_miolo_md(chapters)
    paths.miolo_md.write_text(miolo_md, encoding="utf-8")

    title_page, copyright_text, source_record, about = _read_frontmatter(
        paths.frontmatter_dir
    )
    parts = [
        "# Title Page {.frontmatter-title .unlisted}\n\n" + title_page,
        "# Copyright {.frontmatter-title .unlisted}\n\n" + copyright_text,
        source_record,
        "# About this edition {.frontmatter-title .unlisted}\n\n" + about,
        miolo_md.strip(),
    ]
    final_text = "\n\n".join(p for p in parts if p.strip()) + "\n"

    paths.final_md.write_text(final_text, encoding="utf-8")
    paths.build_md.write_text(final_text, encoding="utf-8")


if __name__ == "__main__":
    build()
