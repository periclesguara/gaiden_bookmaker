from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from . import paths

PAGE_MARKER_RE = re.compile(r"@@P\d{4}@@\s*")
IMAGE_PLACEHOLDER_RE = re.compile(r"\{\{IMAGE:CH\d{2}:\d{2}\}\}")
ROMAN_HEADING_RE = re.compile(r"^([IVXLCDM]+)\s+([A-Z].+)")
ROMAN_GLUE_RE = re.compile(r"([A-Za-z])([IVXLCDM]+)\.")

CHAPTER_PATTERNS = [
    r"^ADVENTURE\s+[IVXLCDM]+\.\s+.*",
    r"^CHAPTER\s+[IVXLCDM]+(\.|:)?\s*.*",
    r"^CAPITULO\s+[IVXLCDM]+(\.|:)?\s*.*",
    r"^[IVXLCDM]+\.\s+.*",
    r"^[IVXLCDM]+$",
]

CHAPTER_RE = re.compile("|".join(f"(?:{p})" for p in CHAPTER_PATTERNS), re.IGNORECASE)


@dataclass
class PreEditionConfig:
    title: str | None = None
    subtitle: str | None = None
    language: str = "en"
    add_pagebreak_before_chapter: bool = True
    center_title: bool = True


def _selected_txt_sources(edition):
    from . import text_source

    sources = text_source.resolve_selected_text_sources(edition)
    if not sources:
        raise FileNotFoundError("No merge_* file found. Run translate/refine/polish first.")
    return sources


def _clean_raw_text(txt: str) -> str:
    txt = PAGE_MARKER_RE.sub("\n\n", txt)
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
    return txt.strip()


def _normalize_txt_for_md(raw: str) -> str:
    cleaned = _clean_raw_text(raw)
    lines = _split_lines(cleaned)
    return "\n".join(lines)


def _is_chapter_heading(line: str) -> bool:
    if not line:
        return False
    return bool(CHAPTER_RE.match(line.strip()))


def _split_lines(txt: str) -> list[str]:
    lines = [ln.rstrip() for ln in txt.split("\n")]
    normalized: list[str] = []
    for line in lines:
        if not line:
            normalized.append(line)
            continue
        if line.lstrip().startswith("#"):
            normalized.append(line)
            continue
        line = ROMAN_GLUE_RE.sub(r"\1\n\2.", line)
        for part in line.split("\n"):
            part = part.rstrip()
            if ROMAN_HEADING_RE.match(part) and not re.match(r"^[IVXLCDM]+\.", part):
                part = ROMAN_HEADING_RE.sub(r"\1. \2", part)
            normalized.append(part)
    return normalized


def _reflow_to_blocks(lines: list[str]) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    buffer_para: list[str] = []

    def flush_para() -> None:
        nonlocal buffer_para, blocks
        if buffer_para:
            para = " ".join(s.strip() for s in buffer_para if s.strip())
            para = re.sub(r"\s{2,}", " ", para).strip()
            if para:
                blocks.append(("para", para))
            buffer_para = []

    for raw_line in lines:
        line = raw_line.strip()

        if not line:
            flush_para()
            continue

        if line.lstrip().startswith("#"):
            flush_para()
            blocks.append(("md_heading", line.strip()))
            continue

        if _is_chapter_heading(line):
            flush_para()
            blocks.append(("chapter", line.strip()))
            continue

        buffer_para.append(line)

    flush_para()
    return blocks


def _markdown_for_title(cfg: PreEditionConfig) -> str:
    parts: list[str] = []

    if not cfg.title:
        return ""

    if cfg.center_title:
        parts.append("::: center")
        parts.append(f"# {cfg.title}")
        if cfg.subtitle:
            parts.append("")
            parts.append(f"## {cfg.subtitle}")
        parts.append(":::")
        parts.append("")
    else:
        parts.append(f"# {cfg.title}")
        if cfg.subtitle:
            parts.append("")
            parts.append(f"## {cfg.subtitle}")
        parts.append("")

    return "\n".join(parts)


def _markdown_from_blocks(blocks: list[tuple[str, str]], cfg: PreEditionConfig) -> str:
    out_lines: list[str] = []

    for kind, text in blocks:
        if kind == "chapter":
            if cfg.add_pagebreak_before_chapter:
                out_lines.append(r"\newpage")
                out_lines.append("")
            out_lines.append(f"# {text.strip()}")
            out_lines.append("")
        elif kind == "md_heading":
            out_lines.append(text.strip())
            out_lines.append("")
        else:
            out_lines.append(text.strip())
            out_lines.append("")

    while out_lines and not out_lines[-1].strip():
        out_lines.pop()

    return "\n".join(out_lines)


def pre_edition_txt_to_md(
    txt_path: str | Path,
    md_path: str | Path,
    cfg: PreEditionConfig | None = None,
) -> Path:
    if cfg is None:
        cfg = PreEditionConfig()

    txt_path = Path(txt_path)
    md_path = Path(md_path)

    raw = txt_path.read_text(encoding="utf-8")
    cleaned = _normalize_txt_for_md(raw)
    lines = cleaned.split("\n")
    blocks = _reflow_to_blocks(lines)

    md_parts: list[str] = []

    title_block = _markdown_for_title(cfg)
    if title_block:
        md_parts.append(title_block)

    body_md = _markdown_from_blocks(blocks, cfg)
    if body_md:
        md_parts.append(body_md)

    final_md = "\n\n".join(md_parts).strip() + "\n"

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(final_md, encoding="utf-8")

    return md_path


def run_txt_to_md(edition) -> Dict[str, str]:
    sources = _selected_txt_sources(edition)
    build_dir = paths.edition_build_dir(edition)
    subtitle = getattr(edition, "subtitle", None) or None
    items: list[dict[str, str]] = []
    for source in sources:
        clean_txt_path = source.path.with_name(f"{source.path.stem}_clean{source.path.suffix}")
        clean_txt_path.parent.mkdir(parents=True, exist_ok=True)
        clean_txt_path.write_text(
            _normalize_txt_for_md(source.path.read_text(encoding="utf-8")),
            encoding="utf-8",
        )
        cfg = PreEditionConfig(
            title=getattr(edition, "title", None),
            subtitle=subtitle,
            language=source.language,
        )
        if len(sources) == 1:
            out_pre_edition = paths.pre_edition_md_path(edition)
            out_pre_qa = paths.pre_qa_md_path(edition)
        else:
            out_pre_edition = build_dir / f"BOOK.PRE_EDITION.{source.language}.md"
            out_pre_qa = build_dir / f"BOOK.PRE_QA.{source.language}.md"
        pre_edition_txt_to_md(source.path, out_pre_edition, cfg)
        md_text = out_pre_edition.read_text(encoding="utf-8")
        out_pre_qa.parent.mkdir(parents=True, exist_ok=True)
        out_pre_qa.write_text(md_text, encoding="utf-8")
        items.append(
            {
                "language": source.language,
                "path": str(out_pre_edition),
                "path_pre_qa": str(out_pre_qa),
            }
        )

    md_text = out_pre_edition.read_text(encoding="utf-8") if items else ""
    result = {
        "md_text": md_text,
        "items": items,
        "path": items[0]["path"] if items else "",
        "path_pre_qa": items[0]["path_pre_qa"] if items else "",
    }
    return result


def insert_page_headlines(md_path: Path, lang: str = "en") -> None:
    text = md_path.read_text(encoding="utf-8")

    lines = text.splitlines()
    out_lines: list[str] = []
    chapter_idx = 0
    found_chapter = False

    for line in lines:
        if line.startswith("## "):
            chapter_idx += 1
            found_chapter = True
            out_lines.append("::: pagebreak")
            out_lines.append(line)
            continue
        if line.startswith("# "):
            heading_text = line[2:].strip()
            if CHAPTER_RE.match(heading_text):
                chapter_idx += 1
                found_chapter = True
                out_lines.append("::: pagebreak")
                out_lines.append(line)
                continue
        out_lines.append(line)

    if not found_chapter:
        heading = "Chapter 01 — [TITLE HERE]"
        if lang.lower().startswith("pt"):
            heading = "Capitulo 01 — [TITULO AQUI]"

        new_text = (
            "::: pagebreak\n"
            f"## {heading}\n\n"
            + text.lstrip()
        )
        md_path.write_text(new_text, encoding="utf-8")
        return

    new_text = "\n".join(out_lines)
    md_path.write_text(new_text, encoding="utf-8")


def insert_image_placeholders(md_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8")

    has_chapter = False
    if re.search(r"^##\s+", text, flags=re.MULTILINE):
        has_chapter = True
    elif re.search(r"^#\s+.+", text, flags=re.MULTILINE):
        for line in text.splitlines():
            if line.startswith("# "):
                if CHAPTER_RE.match(line[2:].strip()):
                    has_chapter = True
                    break

    if not has_chapter:
        if IMAGE_PLACEHOLDER_RE.search(text):
            return
        new_text = "{{IMAGE:CH01:01}}\n\n" + text.lstrip()
        md_path.write_text(new_text, encoding="utf-8")
        return

    lines = text.splitlines()
    out_lines: list[str] = []
    chapter_idx = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        out_lines.append(line)
        is_chapter = False
        if line.startswith("## "):
            is_chapter = True
        elif line.startswith("# ") and CHAPTER_RE.match(line[2:].strip()):
            is_chapter = True

        if is_chapter:
            chapter_idx += 1
            idx_str = f"{chapter_idx:02d}"
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and IMAGE_PLACEHOLDER_RE.search(lines[j]):
                i += 1
                continue
            out_lines.append(f"{{{{IMAGE:CH{idx_str}:01}}}}")
        i += 1

    new_text = "\n".join(out_lines)
    md_path.write_text(new_text, encoding="utf-8")
