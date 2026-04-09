from __future__ import annotations

import re
from pathlib import Path
import shutil
from dataclasses import dataclass
from typing import List, Tuple, Dict

from gaiden.infrastructure import storage

from . import edition_meta, utils

PATTERN_EN = r"^CHAPTER\s+\d+\s*[-–:]?\s*(.*)$"
PATTERN_ES = r"^(CAP[IÍ]TULO)\s+\d+\s*[-–:]?\s*(.*)$"
PATTERN_PTBR = r"^(CAP[IÍ]TULO)\s+\d+\s*[-–:]?\s*(.*)$"


@dataclass
class ChapterPattern:
    name: str
    regex: re.Pattern


CHAPTER_H2_RE = re.compile(r"^\s*##\s+(\d+\.\s+.*)$", re.IGNORECASE)
EXISTING_MD_HEADING_RE = re.compile(
    r"^\s*#{1,6}\s+(?:chapter|kapitel|cap[ií]tulo|chapitre)\s+(?:\d+|[IVXLCDM]+)\b",
    re.IGNORECASE,
)


def promote_chapter_h2_to_h1(md_text: str) -> tuple[str, int]:
    out: list[str] = []
    promoted = 0
    for line in md_text.splitlines():
        m = CHAPTER_H2_RE.match(line)
        if m:
            out.append("# " + m.group(1).strip())
            promoted += 1
        else:
            out.append(line)
    return ("\n".join(out).strip() + "\n"), promoted


CHAPTER_PATTERNS_BY_LANG = {
    "en": [
        ChapterPattern(
            "CHAPTER_NUM_OR_ROMAN",
            re.compile(r"^CHAPTER\s+(?:\d+|[IVXLCDM]+)\s*[-–:.]?\s*(.*)$", re.I),
        ),
    ],
    "de": [
        ChapterPattern("ROMAN_DOT", re.compile(r"^[IVXLCDM]+\.\s+.+$", re.I)),
        ChapterPattern("NUM_DOT", re.compile(r"^\d+\.\s+.+$")),
        ChapterPattern("ALLCAPS", re.compile(r"^[A-ZÄÖÜ][A-ZÄÖÜ0-9 ,;:\\-–'\"!?()]+$", re.U)),
    ],
}


def detect_chapter_lines(lines: list[str], lang: str) -> list[tuple[int, str, str]]:
    patterns = CHAPTER_PATTERNS_BY_LANG.get(lang, [])
    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        for pattern in patterns:
            if pattern.regex.match(s):
                hits.append((i, s, pattern.name))
                break
    return hits


def inject_headings_from_detected(lines: list[str], hits: list[tuple[int, str, str]]) -> str:
    hit_idx = {i for (i, _, _) in hits}
    out: list[str] = []
    for i, line in enumerate(lines):
        s = line.rstrip("\n")
        if i in hit_idx:
            title = s.strip()
            out.append(f"\n# {title}\n")
        else:
            out.append(s)
    return "\n".join(out).strip() + "\n"


def ensure_markdown_headings(md_text: str, lang: str) -> str:
    if any(EXISTING_MD_HEADING_RE.match(line) for line in md_text.splitlines()):
        return md_text if md_text.endswith("\n") else (md_text + "\n")

    promoted_text, promoted_count = promote_chapter_h2_to_h1(md_text)
    if promoted_count > 0:
        return promoted_text

    lines = md_text.splitlines()
    hits = detect_chapter_lines(lines, lang)
    if hits:
        return inject_headings_from_detected(lines, hits)
    return md_text if md_text.endswith("\n") else (md_text + "\n")


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
    lang: str,
) -> Path:
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)

    raw = source.read_text(encoding="utf-8")

    chapters = split_chapters(raw, chapter_pattern)
    if not chapters:
        md = raw.strip() + "\n"
    else:
        md = build_miolo(chapters, chapter_pattern)
    md = ensure_markdown_headings(md, lang)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(md, encoding="utf-8")
    return output


def publish_miolo_for_kdp(edition, miolo_md_path: Path) -> Path:
    target = storage.translated_dir(
        edition_meta.book_code(edition),
        edition_meta.language_code(edition),
    ) / "miolo.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(miolo_md_path, target)
    return target


def run_txt_to_miolo(edition) -> Dict[str, str]:
    from . import paths, text_source

    source = text_source.resolve_txt_source(edition)
    lang = edition.language.code
    pattern = _pattern_for_language(lang)
    out_path = paths.miolo_md_path(edition)
    txt_to_md(source.path, out_path, pattern, lang)
    published_path = publish_miolo_for_kdp(edition, out_path)

    return {
        "md_text": out_path.read_text(encoding="utf-8"),
        "items": [{"language": edition_meta.language_code(edition), "path": str(out_path)}],
        "path": str(out_path),
        "published_miolo": str(published_path),
        "source_txt": str(source.path),
    }


def run_txt_to_miolo_from_reference(edition) -> Dict[str, str]:
    return run_txt_to_miolo(edition)
