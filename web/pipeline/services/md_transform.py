from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from . import paths

PAGE_MARKER_RE = re.compile(r"@@P\d{4}@@\s*")
IMAGE_PLACEHOLDER_RE = re.compile(r"\{\{IMAGE:CH\d{2}:\d{2}\}\}")
IMAGE_PLACEHOLDER_TOKEN_RE = re.compile(r"\{\{IMAGE:CH(\d{2}):(\d{2})\}\}")
IMAGE_MARKDOWN_RE = re.compile(r"!\[CH\d{2}:\d{2}\]\(assets/images/[^)]+\)")
IMAGE_MARKDOWN_ANY_RE = re.compile(r"!\[[^\]]*\]\(assets/images/[^)]+\)")
ROMAN_HEADING_RE = re.compile(r"^([IVXLCDM]+)\s+([A-Z].+)")
ROMAN_GLUE_RE = re.compile(r"([A-Za-z])([IVXLCDM]+)\.")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MD_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")
ROMAN_ONLY_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)
CHAPTER_NUM_RE = re.compile(
    r"^\s*(?:chapter|adventure|cap[ií]tulo)\s+([ivxlcdm]+|\d+)\b",
    re.IGNORECASE,
)
CHAPTER_PREFIX_RE = re.compile(
    r"^\s*(?:chapter|cap[ií]tulo|kapitel)\s+([ivxlcdm]+|\d+)\s*[-.:]?\s*(.*)$",
    re.IGNORECASE,
)
LEADING_NUMERIC_CHAPTER_RE = re.compile(r"^\s*([IVXLCDM]+|\d+)\s*[\.\-:]\s*(.+)$", re.IGNORECASE)
TITLE_SMALL_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "but",
    "by",
    "da",
    "de",
    "del",
    "der",
    "des",
    "die",
    "el",
    "for",
    "from",
    "in",
    "la",
    "le",
    "los",
    "of",
    "on",
    "or",
    "the",
    "to",
    "und",
    "von",
    "with",
    "y",
}

CHAPTER_PATTERNS = [
    r"^ADVENTURE\s+[IVXLCDM]+\.\s+.*",
    r"^CHAPTER\s+[IVXLCDM\d]+(\.|:)?\s*.*",
    r"^CAPITULO\s+[IVXLCDM\d]+(\.|:)?\s*.*",
    r"^[IVXLCDM]+$",
    r"^\d+$",
]

CHAPTER_RE = re.compile("|".join(f"(?:{p})" for p in CHAPTER_PATTERNS), re.IGNORECASE)


@dataclass
class PreEditionConfig:
    title: str | None = None
    subtitle: str | None = None
    book_code: str | None = None
    language: str = "en"
    add_pagebreak_before_chapter: bool = True
    center_title: bool = True


KNOWN_CHAPTER_MARKERS: dict[tuple[str, str], dict[str, list[str]]] = {
    (
        "book10",
        "en",
    ): {
        "titles": [
            "Silver Blaze",
            "The Adventure of the Cardboard Box",
            "The Yellow Face",
            "The Stockbroker's Clerk",
            'The "Gloria Scott"',
            "The Musgrave Ritual",
            "The Reigate Squires",
            "The Crooked Man",
            "The Resident Patient",
            "The Greek Interpreter",
            "The Naval Treaty",
            "The Final Problem",
        ],
        "markers": [
            "In choosing a few representative cases that showcase the remarkable mental powers of my friend Sherlock Holmes",
            "In publishing these short sketches based on the many cases where my companion’s remarkable talents have made us first listeners and then actors in some strange drama",
            "Shortly after my marriage, I bought a medical practice in the Paddington district.",
            "“I’ve got some papers here,” said my friend Sherlock Holmes as we sat one winter night by the fire",
            "“These,” he said, “are all that I have left to remind me of the adventure of the Musgrave Ritual.”",
            "It took quite some time for my friend, Mr. Sherlock Holmes, to recover his health after the strain of his immense exertions in the spring of '87.",
            "One summer night, a few months after my marriage, I sat by the fireplace, smoking a last pipe and nodding over a novel.",
            "Looking back over the somewhat disjointed collection of memoirs with which I’ve tried to illustrate a few of the quirks of my friend Mr. Sherlock Holmes’s mind",
            "During all the years I had known Mr. Sherlock Holmes, he never spoke of his family and rarely of his early life.",
            "The July following my marriage stands out in my memory due to three particularly interesting cases where I had the privilege of working with Sherlock Holmes and observing his methods.",
            "With a heavy heart, I pick up my pen to write these final words about the remarkable gifts that distinguished my friend, Mr. Sherlock Holmes.",
        ],
    }
}


def _selected_txt_sources(edition):
    from . import text_source

    sources = text_source.resolve_selected_text_sources(edition)
    if not sources:
        raise FileNotFoundError("No merge_* file found. Run translate/refine/polish first.")
    return sources


def _selected_txt_sources_for_language(edition, language: str):
    from . import text_source

    book_code = getattr(edition, "work", None)
    if book_code and getattr(book_code, "code", None):
        book_code = book_code.code
    else:
        book_code = getattr(edition, "book_code", "")
    build_dir = paths.edition_build_dir_for_language(book_code, language)
    marker = build_dir / paths.FORCE_MERGE_TRANSLATE_MARKER
    if marker.exists():
        order = ["merge_translate", "merge_refine", "merge_polish"]
    else:
        order = [p.replace(".txt", "") for p in paths.MERGE_PRIORITY]
    candidates: list[Path] = []
    for base in order:
        candidates.append(build_dir / f"{base}_{language}.txt")
        candidates.append(build_dir / f"{base}.txt")
    for path in candidates:
        if path.exists():
            return [
                text_source.SelectedTextSource(
                    language=language,
                    path=path,
                    name=path.name,
                    label=f"{path.name} ({language})",
                )
            ]
    for path in sorted(build_dir.glob("*.txt")):
        return [
            text_source.SelectedTextSource(
                language=language,
                path=path,
                name=path.name,
                label=f"{path.name} ({language})",
            )
        ]
    raise FileNotFoundError(f"No merge_* file found for language {language}.")


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
    stripped = line.strip()
    upper = stripped.upper()
    if "|" in stripped:
        return False
    if upper in {"CONTENTS", "TABLE OF CONTENTS"}:
        return False
    if upper.startswith("FIRST PUBLISHED"):
        return False
    if upper in {"BY ARTHUR CONAN DOYLE", "ARTHUR CONAN DOYLE"}:
        return False
    if CHAPTER_PREFIX_RE.match(stripped):
        return True
    if re.match(r"^ADVENTURE\s+[IVXLCDM]+\.\s+.*", stripped, re.IGNORECASE):
        return True
    numeric = LEADING_NUMERIC_CHAPTER_RE.match(stripped)
    if numeric:
        return _looks_like_story_title(numeric.group(2))
    return _looks_like_story_title(stripped)


def _md_heading_text(line: str) -> str | None:
    match = MD_HEADING_RE.match(line or "")
    if not match:
        return None
    return match.group(1).strip()


def _is_md_chapter_heading(line: str) -> bool:
    heading_text = _md_heading_text(line)
    if not heading_text:
        return False
    return _is_chapter_heading(heading_text)


def _roman_to_int(value: str) -> int | None:
    table = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed(value.upper()):
        v = table.get(ch)
        if v is None:
            return None
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    return total if total > 0 else None


def _extract_chapter_number(heading_text: str) -> int | None:
    match = CHAPTER_NUM_RE.match((heading_text or "").strip())
    if not match:
        prefixed = CHAPTER_PREFIX_RE.match((heading_text or "").strip())
        if prefixed:
            token = prefixed.group(1)
        else:
            numeric = LEADING_NUMERIC_CHAPTER_RE.match((heading_text or "").strip())
            if not numeric:
                return None
            token = numeric.group(1)
    else:
        token = match.group(1)
    if token.isdigit():
        return int(token)
    return _roman_to_int(token)


def _looks_like_story_title(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped:
        return False
    if len(stripped) > 120:
        return False
    if stripped[0] in {'"', "'", "“", "‘", "(", "["}:
        return False
    if stripped[-1] in {".", "!", "?", ",", ";", ":"}:
        return False
    if any(ch in stripped for ch in {"@", "{", "}", "`"}):
        return False

    words = re.findall(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9'’-]*", stripped)
    if len(words) < 2 or len(words) > 12:
        return False

    recognized = 0
    alpha_words = 0
    for idx, word in enumerate(words):
        lower = word.lower()
        if any(ch.isalpha() for ch in word):
            alpha_words += 1
        if re.fullmatch(r"[IVXLCDM]+", word, re.IGNORECASE):
            recognized += 1
            continue
        if word.isdigit():
            recognized += 1
            continue
        if idx > 0 and lower in TITLE_SMALL_WORDS:
            recognized += 1
            continue
        if word.isupper() and len(word) > 1:
            continue
        if word[0].isupper():
            recognized += 1
            continue

    if alpha_words < 2:
        return False
    if len(words) <= 2 and not any(word[0].isupper() and not word.isupper() for word in words if word):
        return False
    return recognized >= max(len(words) - 1, 2)


def _chapter_label_for_language(language: str) -> str:
    lang = (language or "").lower()
    if lang.startswith("pt") or lang.startswith("es"):
        return "Capitulo"
    if lang.startswith("de"):
        return "Kapitel"
    return "Chapter"


def _chapter_title_only(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        return stripped

    prefixed = CHAPTER_PREFIX_RE.match(stripped)
    if prefixed:
        title = prefixed.group(2).strip()
        return title or stripped

    numeric = LEADING_NUMERIC_CHAPTER_RE.match(stripped)
    if numeric:
        return numeric.group(2).strip()

    return stripped


def _format_chapter_heading(text: str, chapter_no: int, language: str) -> str:
    label = _chapter_label_for_language(language)
    title = _chapter_title_only(text)
    return f"{label} {chapter_no:02d} - {title}"


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


def _source_md_path(cfg: PreEditionConfig) -> Path | None:
    book_code = (cfg.book_code or "").strip()
    language = (cfg.language or "").strip().lower()
    if not book_code or not language:
        return None
    path = paths.data_dir() / "md" / book_code / f"{book_code}_{language}_source.md"
    return path if path.exists() else None


def _book_numeric_code(book_code: str | None) -> int | None:
    if not book_code:
        return None
    digits = "".join(ch for ch in str(book_code) if ch.isdigit())
    if not digits:
        return None
    return int(digits)


def _normalize_source_heading(text: str) -> str:
    stripped = " ".join((text or "").replace("\\", "").split()).strip()
    if not stripped:
        return stripped
    if not stripped.isupper():
        return stripped

    words = re.split(r"(\s+)", stripped.lower())
    converted: list[str] = []
    first_word = True
    for token in words:
        if not token or token.isspace():
            converted.append(token)
            continue
        if token in TITLE_SMALL_WORDS and not first_word:
            converted.append(token)
        else:
            converted.append(token[:1].upper() + token[1:])
        first_word = False
    return "".join(converted)


def _looks_like_source_story_heading(text: str, cfg: PreEditionConfig) -> bool:
    stripped = _normalize_source_heading(text)
    if not stripped:
        return False
    upper = stripped.upper()
    book_title = (cfg.title or "").strip().upper()
    if book_title and upper == book_title:
        return False
    if upper in {"CONTENTS", "TABLE OF CONTENTS"}:
        return False
    if ROMAN_ONLY_RE.fullmatch(stripped):
        return False
    if upper.startswith("BY "):
        return False
    return upper.startswith("THE ADVENTURE OF ") or upper.startswith("THE PROBLEM OF ")


def _extract_source_md_markers(cfg: PreEditionConfig) -> dict[str, list[str]] | None:
    source_md_path = _source_md_path(cfg)
    if source_md_path is None:
        return None

    lines = source_md_path.read_text(encoding="utf-8").splitlines()
    titles: list[str] = []
    markers: list[str] = []

    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        if not line.startswith("### "):
            idx += 1
            continue

        heading = line[4:].strip()
        if not _looks_like_source_story_heading(heading, cfg):
            idx += 1
            continue

        title = _normalize_source_heading(heading)
        idx += 1
        paragraph_lines: list[str] = []
        while idx < len(lines):
            current = lines[idx].strip()
            if not current:
                if paragraph_lines:
                    break
                idx += 1
                continue
            if current.startswith("#"):
                break
            if current.startswith("[") or current.startswith("![]("):
                idx += 1
                continue
            paragraph_lines.append(current)
            idx += 1

        marker = " ".join(paragraph_lines).strip()
        if marker:
            titles.append(title)
            markers.append(marker)

    if not titles or len(titles) != len(markers):
        return None
    return {"titles": titles, "markers": markers}


def _markdown_from_source_md_markers(txt: str, cfg: PreEditionConfig) -> str | None:
    spec = _extract_source_md_markers(cfg)
    if not spec:
        return None

    titles = spec["titles"]
    markers = spec["markers"]
    starts: list[int] = []
    search_from = 0
    for marker in markers:
        idx = txt.find(marker, search_from)
        if idx == -1:
            return None
        starts.append(idx)
        search_from = idx + len(marker)

    starts.append(len(txt))
    out_lines: list[str] = []
    prefix = txt[:starts[0]].strip()
    if prefix:
        out_lines.append(prefix)
        out_lines.append("")

    for idx, title in enumerate(titles):
        segment = txt[starts[idx] : starts[idx + 1]].strip()
        if not segment:
            continue
        if cfg.add_pagebreak_before_chapter:
            out_lines.append(r"\newpage")
            out_lines.append("")
        out_lines.append(f"# {_format_chapter_heading(title, idx + 1, cfg.language)}")
        out_lines.append("")
        out_lines.append(segment)
        out_lines.append("")

    while out_lines and not out_lines[-1].strip():
        out_lines.pop()
    return "\n".join(out_lines)


def _translated_chunk_dir_for_cfg(cfg: PreEditionConfig, txt_path: Path) -> Path | None:
    book_num = _book_numeric_code(cfg.book_code)
    if book_num is None:
        return None

    translated_root = paths.data_dir() / "translated" / f"book_{book_num:04d}"
    if not translated_root.exists():
        return None

    chunk_variants = sorted(
        p for p in translated_root.iterdir()
        if p.is_dir()
    )
    if not chunk_variants:
        return None

    wants_refine = "refine" in txt_path.name.lower()
    if wants_refine:
        for variant in chunk_variants:
            candidate = variant / "return_aldebaran"
            if candidate.exists():
                return candidate
    for variant in chunk_variants:
        txt_files = sorted(p for p in variant.glob("*.txt") if not p.name.startswith("merged_"))
        if txt_files:
            return variant
    return None


def _extract_split_chapter_map(cfg: PreEditionConfig) -> list[tuple[str, str]]:
    book_num = _book_numeric_code(cfg.book_code)
    if book_num is None:
        return []

    split_dir = paths.data_dir() / "chunks" / f"book_{book_num:04d}" / "split_01"
    if not split_dir.exists():
        return []

    chapters: list[tuple[str, str]] = []
    for chunk_path in sorted(split_dir.glob("*.txt")):
        for line in chunk_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith("### "):
                continue
            heading = stripped[4:].strip()
            if not _looks_like_source_story_heading(heading, cfg):
                continue
            chapters.append((chunk_path.name, _normalize_source_heading(heading)))
            break
    return chapters


def _markdown_from_chunk_boundaries(txt_path: Path, cfg: PreEditionConfig) -> str | None:
    chapter_map = _extract_split_chapter_map(cfg)
    if not chapter_map:
        return None

    chunk_dir = _translated_chunk_dir_for_cfg(cfg, txt_path)
    if chunk_dir is None or not chunk_dir.exists():
        return None

    available_chunks = {
        path.name: path.read_text(encoding="utf-8").strip()
        for path in sorted(chunk_dir.glob("*.txt"))
        if not path.name.startswith("merged_")
    }
    if not available_chunks:
        return None

    ordered_names = sorted(available_chunks.keys())
    chapter_starts = [name for name, _title in chapter_map if name in available_chunks]
    if len(chapter_starts) != len(chapter_map):
        return None

    prefix_names = [name for name in ordered_names if name < chapter_starts[0]]
    out_lines: list[str] = []
    if prefix_names:
        prefix_parts = [available_chunks[name] for name in prefix_names if available_chunks[name]]
        prefix_text = "\n\n".join(prefix_parts).strip()
        if prefix_text:
            out_lines.append(prefix_text)
            out_lines.append("")

    for idx, (start_name, title) in enumerate(chapter_map):
        start_index = ordered_names.index(start_name)
        end_name = chapter_map[idx + 1][0] if idx + 1 < len(chapter_map) else None
        end_index = ordered_names.index(end_name) if end_name else len(ordered_names)
        chapter_names = ordered_names[start_index:end_index]
        chapter_parts = [available_chunks[name] for name in chapter_names if available_chunks[name]]
        chapter_text = "\n\n".join(chapter_parts).strip()
        if not chapter_text:
            continue
        if cfg.add_pagebreak_before_chapter:
            out_lines.append(r"\newpage")
            out_lines.append("")
        out_lines.append(f"# {_format_chapter_heading(title, idx + 1, cfg.language)}")
        out_lines.append("")
        out_lines.append(chapter_text)
        out_lines.append("")

    while out_lines and not out_lines[-1].strip():
        out_lines.pop()
    return "\n".join(out_lines) if out_lines else None


def _markdown_from_known_chapter_markers(txt: str, cfg: PreEditionConfig) -> str | None:
    book_code = (cfg.book_code or "").strip()
    spec = KNOWN_CHAPTER_MARKERS.get((book_code, (cfg.language or "").lower()))
    if not spec:
        return None

    titles = spec["titles"]
    markers = spec["markers"]
    starts = [0]
    for marker in markers:
        idx = txt.find(marker)
        if idx == -1:
            return None
        starts.append(idx)
    if starts != sorted(starts):
        return None
    starts.append(len(txt))

    out_lines: list[str] = []
    for idx, title in enumerate(titles):
        segment = txt[starts[idx] : starts[idx + 1]].strip()
        if not segment:
            continue
        if cfg.add_pagebreak_before_chapter:
            out_lines.append(r"\newpage")
            out_lines.append("")
        out_lines.append(f"# {_format_chapter_heading(title, idx + 1, cfg.language)}")
        out_lines.append("")
        out_lines.append(segment)
        out_lines.append("")

    while out_lines and not out_lines[-1].strip():
        out_lines.pop()
    return "\n".join(out_lines)


def _markdown_from_blocks(blocks: list[tuple[str, str]], cfg: PreEditionConfig) -> str:
    out_lines: list[str] = []
    chapter_no = 0

    for kind, text in blocks:
        if kind == "chapter":
            chapter_no += 1
            if cfg.add_pagebreak_before_chapter:
                out_lines.append(r"\newpage")
                out_lines.append("")
            out_lines.append(f"# {_format_chapter_heading(text.strip(), chapter_no, cfg.language)}")
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

    md_parts: list[str] = []

    title_block = _markdown_for_title(cfg)
    if title_block:
        md_parts.append(title_block)

    body_md = _markdown_from_known_chapter_markers(cleaned, cfg)
    if body_md is None:
        body_md = _markdown_from_source_md_markers(cleaned, cfg)
    if body_md is None:
        body_md = _markdown_from_chunk_boundaries(txt_path, cfg)
    if body_md is None:
        lines = cleaned.split("\n")
        blocks = _reflow_to_blocks(lines)
        body_md = _markdown_from_blocks(blocks, cfg)
    if body_md:
        md_parts.append(body_md)

    final_md = "\n\n".join(md_parts).strip() + "\n"

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(final_md, encoding="utf-8")

    return md_path


def run_txt_to_md(edition, language_override: str | None = None) -> Dict[str, str]:
    if language_override:
        sources = _selected_txt_sources_for_language(edition, language_override)
        build_dir = paths.edition_build_dir_for_language(
            getattr(getattr(edition, "work", None), "code", "") or getattr(edition, "book_code", ""),
            language_override,
        )
    else:
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
            book_code=getattr(getattr(edition, "work", None), "code", None),
            language=source.language,
        )
        if len(sources) == 1:
            out_pre_edition = build_dir / "BOOK.PRE_EDITION.md"
            out_pre_qa = build_dir / "BOOK.PRE_QA.md"
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
    pending_pagebreak = False
    inside_center_block = False

    for line in lines:
        stripped = line.strip()
        if stripped == r"\newpage":
            pending_pagebreak = True
            out_lines.append(line)
            continue
        if stripped.startswith("::: center"):
            inside_center_block = True
            pending_pagebreak = False
            out_lines.append(line)
            continue
        if inside_center_block and stripped == ":::":
            inside_center_block = False
            pending_pagebreak = False
            out_lines.append(line)
            continue
        if inside_center_block:
            out_lines.append(line)
            continue
        if line.startswith("# "):
            heading_text = line[2:].strip()
            if _is_chapter_heading(heading_text):
                chapter_idx += 1
                found_chapter = True
                if not pending_pagebreak:
                    out_lines.append(r"\newpage")
                    out_lines.append("")
                out_lines.append(f"# {_format_chapter_heading(heading_text, chapter_idx, lang)}")
                out_lines.append("")
                pending_pagebreak = False
                continue
        pending_pagebreak = False
        out_lines.append(line)

    if not found_chapter:
        heading = _format_chapter_heading("[TITLE HERE]", 1, lang)
        if lang.lower().startswith("pt") or lang.lower().startswith("es"):
            heading = _format_chapter_heading("[TITULO AQUI]", 1, lang)

        new_text = (
            "\\newpage\n\n"
            f"# {heading}\n\n"
            + text.lstrip()
        )
        md_path.write_text(new_text, encoding="utf-8")
        return

    new_text = "\n".join(out_lines)
    md_path.write_text(new_text, encoding="utf-8")


def insert_image_placeholders(md_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8")
    # Rebuild placeholders from scratch so old misplaced placeholders
    # do not accumulate across repeated runs.
    text = IMAGE_PLACEHOLDER_RE.sub("", text)
    text = IMAGE_MARKDOWN_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    inside_center_block = False
    has_chapter = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("::: center"):
            inside_center_block = True
            continue
        if inside_center_block and stripped == ":::": 
            inside_center_block = False
            continue
        if inside_center_block:
            continue
        if _is_md_chapter_heading(line):
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
    seen_chapters: set[int] = set()
    inside_center_block = False
    i = 0
    while i < len(lines):
        line = lines[i]
        out_lines.append(line)
        stripped = line.strip()
        if stripped.startswith("::: center"):
            inside_center_block = True
            i += 1
            continue
        if inside_center_block and stripped == ":::": 
            inside_center_block = False
            i += 1
            continue
        if inside_center_block:
            i += 1
            continue
        is_chapter = _is_md_chapter_heading(line)

        if is_chapter:
            chapter_text = _md_heading_text(line) or ""
            chapter_no = _extract_chapter_number(chapter_text)
            if chapter_no is None:
                chapter_idx += 1
                chapter_no = chapter_idx
            if chapter_no in seen_chapters:
                i += 1
                continue
            seen_chapters.add(chapter_no)
            idx_str = f"{chapter_no:02d}"
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


def _natural_sort_key(path: Path) -> tuple:
    parts = re.split(r"(\d+)", path.name.lower())
    key: list[object] = []
    for part in parts:
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part)
    return tuple(key)


def list_available_images(images_dir: Path) -> list[Path]:
    if not images_dir.exists():
        return []
    items = [
        path
        for path in images_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(items, key=_natural_sort_key)


def _normalize_stem(path: Path) -> str:
    return re.sub(r"[^a-z0-9]+", "", path.stem.lower())


def _extract_numbers(path: Path) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", path.stem)]


def _is_cover_asset(path: Path) -> bool:
    stem = re.sub(r"[^a-z0-9]+", "", path.stem.lower())
    if stem in {"00", "0", "cover", "capa", "frontcover"}:
        return True
    nums = _extract_numbers(path)
    return bool(nums) and nums[0] == 0


def _score_candidate(path: Path, chapter: int, slot: int) -> int | None:
    nums = _extract_numbers(path)
    norm = _normalize_stem(path)
    ch = f"{chapter:02d}"
    sl = f"{slot:02d}"
    if len(nums) >= 2 and nums[0] == chapter and nums[1] == slot:
        return 0
    if f"ch{ch}{sl}" in norm or f"chapter{ch}{sl}" in norm:
        return 0
    if f"ch{ch}" in norm and sl in norm:
        return 1
    if len(nums) >= 1 and nums[0] == chapter:
        return 2
    return None


def _pick_image_for_placeholder(
    images: list[Path],
    used: set[Path],
    chapter: int,
    slot: int,
) -> Path | None:
    scored: list[tuple[int, int, Path]] = []
    for idx, path in enumerate(images):
        if path in used:
            continue
        score = _score_candidate(path, chapter, slot)
        if score is None:
            continue
        scored.append((score, idx, path))
    if scored:
        scored.sort(key=lambda row: (row[0], row[1]))
        return scored[0][2]

    # Fallback: first remaining image in natural order.
    for path in images:
        if path not in used:
            return path
    return None


def _safe_copy_name(chapter: int, slot: int, src: Path) -> str:
    base = re.sub(r"[^a-zA-Z0-9._-]+", "_", src.stem).strip("._-") or "image"
    return f"ch{chapter:02d}_{slot:02d}_{base}{src.suffix.lower()}"


def apply_images_to_pre_edition(md_path: Path, images_dir: Path) -> dict[str, int | str]:
    text = md_path.read_text(encoding="utf-8")
    placeholders = list(IMAGE_PLACEHOLDER_TOKEN_RE.finditer(text))
    existing_refs = len(IMAGE_MARKDOWN_ANY_RE.findall(text))
    if not placeholders:
        return {
            "placeholders_total": 0,
            "inserted": 0,
            "unresolved": 0,
            "images_available": 0,
            "images_used": 0,
            "assets_dir": "",
            "existing_refs": existing_refs,
            "already_applied": 1 if existing_refs > 0 else 0,
        }

    images = [p for p in list_available_images(images_dir) if not _is_cover_asset(p)]
    assets_dir = md_path.parent / "assets" / "images"
    assets_dir.mkdir(parents=True, exist_ok=True)

    used: set[Path] = set()
    mapping: dict[str, Path] = {}
    copied: dict[Path, str] = {}
    inserted = 0
    unresolved = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal inserted, unresolved
        chapter_s, slot_s = match.group(1), match.group(2)
        key = f"{chapter_s}:{slot_s}"
        chapter = int(chapter_s)
        slot = int(slot_s)

        chosen = mapping.get(key)
        if chosen is None:
            chosen = _pick_image_for_placeholder(images, used, chapter, slot)
            if chosen is None:
                unresolved += 1
                return match.group(0)
            mapping[key] = chosen
            used.add(chosen)

        out_name = copied.get(chosen)
        if out_name is None:
            if chosen.resolve().parent == assets_dir.resolve():
                out_name = chosen.name
            else:
                out_name = _safe_copy_name(chapter, slot, chosen)
                shutil.copy2(chosen, assets_dir / out_name)
            copied[chosen] = out_name

        inserted += 1
        rel_path = Path("assets") / "images" / out_name
        return f"![]({rel_path.as_posix()})"

    updated = IMAGE_PLACEHOLDER_TOKEN_RE.sub(repl, text)
    md_path.write_text(updated, encoding="utf-8")

    return {
        "placeholders_total": len(placeholders),
        "inserted": inserted,
        "unresolved": unresolved,
        "images_available": len(images),
        "images_used": len(used),
        "assets_dir": str(assets_dir),
        "existing_refs": len(IMAGE_MARKDOWN_ANY_RE.findall(updated)),
        "already_applied": 1 if inserted == 0 and len(IMAGE_MARKDOWN_ANY_RE.findall(updated)) > 0 else 0,
    }
