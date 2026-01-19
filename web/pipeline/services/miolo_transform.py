from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple, Dict

from . import edition_meta, utils

PATTERN_EN = r"^CHAPTER\s+\d+\s*[-–:]?\s*(.*)$"
PATTERN_ES = r"^(CAP[IÍ]TULO)\s+\d+\s*[-–:]?\s*(.*)$"
PATTERN_PTBR = r"^(CAP[IÍ]TULO)\s+\d+\s*[-–:]?\s*(.*)$"


def _pattern_for_language(language: str) -> str:
    lang = utils.normalize_lang(language)
    if lang == "es":
        return PATTERN_ES
    if lang == "ptbr":
        return PATTERN_PTBR
    return PATTERN_EN


def split_chapters(raw_text: str, header_pattern: str) -> List[Tuple[str, str]]:
    """Divide o TXT em capítulos baseado no pattern."""
    pattern = re.compile(header_pattern, flags=re.IGNORECASE)
    lines = raw_text.splitlines()

    chapters: List[Tuple[str, str]] = []
    current_title = None
    buffer: list[str] = []

    for line in lines:
        m = pattern.match(line.strip())
        if m:
            if current_title is not None:
                chapters.append((current_title, "\n".join(buffer).strip()))
                buffer = []
            current_title = line.strip()
        else:
            buffer.append(line)

    if current_title is not None:
        chapters.append((current_title, "\n".join(buffer).strip()))

    return chapters


def normalize_title(raw: str, header_pattern: str) -> str:
    """Converte 'CHAPTER 1 - TITLE' -> 'TITLE'."""
    for sep in ("-", "–", "—", ":"):
        if sep in raw:
            return raw.split(sep, 1)[1].strip()

    m = re.match(header_pattern, raw, flags=re.IGNORECASE)
    if m:
        groups = [g for g in m.groups() if g]
        if groups:
            return groups[-1].strip()

    return raw.strip()


def build_miolo(chapters: List[Tuple[str, str]], header_pattern: str) -> str:
    """
    Regras do MD:
    - cada capítulo abre com \newpage (exceto o primeiro)
    - título = '# Título'
    - NÃO colar título no corpo -> 1 linha em branco
    - 1 linha em branco ao final
    """
    out: list[str] = []
    first = True

    for raw_title, body in chapters:
        title = normalize_title(raw_title, header_pattern)

        if not first:
            out.append(r"\newpage")
            out.append("")
        first = False

        out.append(f"# {title}")
        out.append("")
        if body:
            out.append(body.strip())
        out.append("")

    return "\n".join(out)


def txt_to_md(
    source: str | Path,
    output: str | Path,
    chapter_pattern: str,
) -> Path:
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)

    raw = source.read_text(encoding="utf-8")

    chapters = split_chapters(raw, chapter_pattern)
    if not chapters:
        raise ValueError(f"Nenhum capítulo usando pattern: {chapter_pattern}")

    md = build_miolo(chapters, chapter_pattern)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(md, encoding="utf-8")
    return output


def run_txt_to_miolo(edition) -> Dict[str, str]:
    from . import paths, text_source

    sources = text_source.resolve_selected_text_sources(edition)
    if not sources:
        raise FileNotFoundError("No merge_* file found. Run translate/refine/polish first.")

    items: list[dict[str, str]] = []

    for source in sources:
        pattern = _pattern_for_language(source.language)
        build_dir = paths.edition_build_dir_for_language(edition_meta.book_code(edition), source.language)
        out_path = paths.miolo_md_path_for_language(
            edition_meta.book_code(edition),
            source.language,
        )
        txt_to_md(source.path, out_path, pattern)
        items.append(
            {
                "language": source.language,
                "path": str(out_path),
            }
        )

    md_text = Path(items[0]["path"]).read_text(encoding="utf-8") if items else ""
    return {
        "md_text": md_text,
        "items": items,
        "path": items[0]["path"] if items else "",
    }
