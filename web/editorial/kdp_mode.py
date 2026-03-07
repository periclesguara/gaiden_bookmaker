from __future__ import annotations

import json
import re
import shutil
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
_IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\(assets/images/([^)]+)\)")
_CH_SLOT_RE = re.compile(r"ch(\d{2})_(\d{2})", re.IGNORECASE)
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_CHAPTER_HEADING_RE = re.compile(
    r"^(#{1,6})\s*(chapter|adventure|cap[ií]tulo|kapitel)\b",
    re.IGNORECASE,
)
_MANUAL_TOC_HEADING_RE = re.compile(
    r"^\s*#{1,6}\s*(contents|table of contents|indice|índice)\s*$",
    re.IGNORECASE,
)
_PLAIN_CHAPTER_LINE_RE = re.compile(
    r"^\s*(chapter|adventure|cap[ií]tulo|kapitel)\s+([ivxlcdm]+|\d+)\b(.*)$",
    re.IGNORECASE,
)
_CHAPTER_MD_LINE_RE = re.compile(
    r"^\s*#{1,6}\s*(chapter|adventure|cap[ií]tulo|kapitel)\s+([ivxlcdm]+|\d+)\b(.*)$",
    re.IGNORECASE,
)
_BOLD_LINE_RE = re.compile(r"^\*\*(.+?)\*\*$")
_IMAGE_LINE_RE = re.compile(r"^!\[[^\]]*\]\([^)]+\)$")
_UNWANTED_TAGLINE_RE = re.compile(
    r"^\s*\*?\s*Another Adventure of Sherlock Holmes\s*\*?\s*$",
    re.IGNORECASE,
)
_LEAKED_MARKER_TOKEN = r"(?:CH|CAP|CHAPTER)\d{1,3}:\d{1,3}"
_FULL_LINE_MARKER_RE = re.compile(rf"(?m)^[ \t]*{_LEAKED_MARKER_TOKEN}[ \t]*$(?:\n)?")
_PARA_LEAD_MARKER_RE = re.compile(
    rf"(?m)(^|\n)([ \t]*){_LEAKED_MARKER_TOKEN}[ \t]+(?=[A-Z“\"'(\[])"
)
_ANY_MARKER_RE = re.compile(_LEAKED_MARKER_TOKEN)
_TRIPLE_BLANKS_RE = re.compile(r"\n{3,}")
_TECH_IMAGE_ALT_RE = re.compile(
    rf"!\[(?P<token>{_LEAKED_MARKER_TOKEN})\]\((?P<path>assets/images/[^)]+)\)"
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


def _image_key_from_name(name: str) -> tuple[int, int] | None:
    stem = Path(name).stem.strip().lower()
    m = _CH_SLOT_RE.search(stem)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = re.fullmatch(r"0*(\d{1,2})", stem)
    if m:
        chapter = int(m.group(1))
        return (chapter, 1)
    return None


def _collect_image_candidates(edition: Edition, builds_base: Path) -> dict[tuple[int, int], Path]:
    book = edition.work.code
    lang = edition.language.code
    roots = [
        builds_base / "assets" / "images",
        Path("data") / "images" / book / lang,
        Path("data") / "images" / book / lang / "consolidated",
    ]

    by_key: dict[tuple[int, int], Path] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.suffix.lower() not in _IMAGE_EXTS:
                continue
            key = _image_key_from_name(path.name)
            if key and key not in by_key:
                by_key[key] = path
    return by_key


def _repair_missing_referenced_assets(edition: Edition, builds_base: Path, merged_text: str) -> None:
    """
    Ensure every markdown reference assets/images/<name> exists.
    If naming changed between runs (e.g., ch01_01.jpg vs ch01_01_01.jpg),
    recreate missing aliases from available image sources.
    """
    refs = sorted(set(_IMAGE_REF_RE.findall(merged_text)))
    if not refs:
        return

    assets_dir = builds_base / "assets" / "images"
    assets_dir.mkdir(parents=True, exist_ok=True)
    candidates = _collect_image_candidates(edition, builds_base)

    for ref_name in refs:
        target = assets_dir / ref_name
        if target.exists():
            continue
        key = _image_key_from_name(ref_name)
        source = candidates.get(key) if key else None
        if source and source.resolve() != target.resolve():
            shutil.copy2(source, target)


def _remove_manual_contents_block(md_text: str) -> str:
    """
    Remove manually authored "Contents" block inside miolo.
    EPUB TOC will be generated automatically by Pandoc/nav.
    """
    lines = md_text.splitlines()
    start = None
    for i, line in enumerate(lines[:300]):
        if _MANUAL_TOC_HEADING_RE.match(line.strip()):
            start = i
            break
    if start is None:
        return md_text

    end = len(lines)
    for j in range(start + 1, len(lines)):
        if _CHAPTER_HEADING_RE.match(lines[j].strip()) or _PLAIN_CHAPTER_LINE_RE.match(lines[j].strip()):
            end = j
            break
    kept = lines[:start] + lines[end:]
    return "\n".join(kept).strip() + "\n"


def _remove_unwanted_taglines(md_text: str) -> str:
    lines = []
    for raw in md_text.splitlines():
        if _UNWANTED_TAGLINE_RE.match(raw.strip()):
            continue
        lines.append(raw)
    return "\n".join(lines).strip() + "\n"


def _marker_preview(text: str, start: int, end: int, radius: int = 60) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return text[lo:hi].replace("\n", "\\n")


def _find_leaked_marker_matches(md_text: str) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    seen: set[tuple[int, str]] = set()
    for kind, rx in (
        ("full_line", _FULL_LINE_MARKER_RE),
        ("paragraph_lead", _PARA_LEAD_MARKER_RE),
    ):
        for match in rx.finditer(md_text):
            line_no = md_text.count("\n", 0, match.start()) + 1
            preview = _marker_preview(md_text, match.start(), match.end())
            token_match = _ANY_MARKER_RE.search(match.group(0))
            token = token_match.group(0) if token_match else match.group(0).strip()
            signature = (line_no, token)
            if signature in seen:
                continue
            seen.add(signature)
            matches.append(
                {
                    "kind": kind,
                    "line": line_no,
                    "match": match.group(0),
                    "preview": preview,
                }
            )
    return matches


def _assert_no_marker_in_headings(md_text: str) -> None:
    for line_no, raw in enumerate(md_text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped.startswith("#"):
            continue
        if _ANY_MARKER_RE.search(stripped):
            raise RuntimeError(
                f"Marker cleanup requires manual review: leaked marker inside heading at line {line_no}: {stripped}"
            )


def _clean_leaked_body_markers(md_text: str) -> tuple[str, list[dict[str, object]]]:
    _assert_no_marker_in_headings(md_text)
    matches = _find_leaked_marker_matches(md_text)
    cleaned = _FULL_LINE_MARKER_RE.sub("", md_text)
    cleaned = _PARA_LEAD_MARKER_RE.sub(r"\1\2", cleaned)
    cleaned = _TRIPLE_BLANKS_RE.sub("\n\n", cleaned)
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines()).strip() + "\n"
    return cleaned, matches


def _clean_technical_image_alt_markers(md_text: str) -> tuple[str, list[dict[str, object]]]:
    matches: list[dict[str, object]] = []
    for match in _TECH_IMAGE_ALT_RE.finditer(md_text):
        line_no = md_text.count("\n", 0, match.start()) + 1
        matches.append(
            {
                "kind": "image_alt",
                "line": line_no,
                "match": match.group(0),
                "preview": _marker_preview(md_text, match.start(), match.end()),
            }
        )
    cleaned = _TECH_IMAGE_ALT_RE.sub(r"![](\g<path>)", md_text)
    return cleaned, matches


def _write_marker_cleanup_report(builds_base: Path, matches: list[dict[str, object]]) -> Path:
    report_path = builds_base / "BOOK.MARKER_CLEANUP_REPORT.json"
    report_path.write_text(json.dumps(matches, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def _normalize_chapter_headings(md_text: str) -> str:
    """
    Make chapter headings explicit and stable for EPUB TOC:
    - normalize "### Chapter X" / "# Chapter X" to "## Chapter X"
    - convert plain/bold chapter lines to markdown heading
    - ensure a blank line before chapter headings
    """
    def _roman_to_int(token: str) -> int | None:
        token = (token or "").strip().upper()
        if not token or not re.fullmatch(r"[IVXLCDM]+", token):
            return None
        values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
        total = 0
        prev = 0
        for ch in reversed(token):
            val = values[ch]
            if val < prev:
                total -= val
            else:
                total += val
                prev = val
        return total if total > 0 else None

    def _chapter_key(text: str) -> tuple[str, str | int] | None:
        m = _PLAIN_CHAPTER_LINE_RE.match(text.strip())
        if not m:
            return None
        prefix = m.group(1).strip().lower()
        num = m.group(2).strip()
        if num.isdigit():
            key_num: str | int = int(num)
        else:
            parsed = _roman_to_int(num)
            key_num = parsed if parsed is not None else num.lower()
        return (prefix, key_num)

    def _chapter_text_with_subtitle(chapter_text: str, lines: list[str], idx: int) -> tuple[str, set[int]]:
        m = _PLAIN_CHAPTER_LINE_RE.match(chapter_text.strip())
        if not m:
            return chapter_text.strip(), set()
        prefix = m.group(1).capitalize()
        num = m.group(2).strip()
        tail = (m.group(3) or "").strip().lstrip(" .:-–—")
        consumed: set[int] = set()

        if not tail:
            # If a bold title appears soon after (optionally after image), merge it into the heading.
            for look_ahead in range(1, 9):
                j = idx + look_ahead
                if j >= len(lines):
                    break
                probe = lines[j].strip()
                if not probe:
                    continue
                if _IMAGE_LINE_RE.match(probe):
                    continue
                bold = _BOLD_LINE_RE.match(probe)
                if bold:
                    candidate = bold.group(1).strip()
                    if candidate and not _PLAIN_CHAPTER_LINE_RE.match(candidate):
                        tail = candidate
                        consumed.add(j)
                break

        if tail:
            return f"{prefix} {num} - {tail}", consumed
        return f"{prefix} {num}", consumed

    src_lines = md_text.splitlines()
    out: list[str] = []
    consumed_lines: set[int] = set()
    i = 0
    while i < len(src_lines):
        if i in consumed_lines:
            i += 1
            continue

        raw = src_lines[i]
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            out.append("")
            i += 1
            continue

        chapter_text = None
        m_h = _CHAPTER_HEADING_RE.match(stripped)
        if m_h:
            chapter_text = stripped.lstrip("#").strip()
        else:
            m_bold = _BOLD_LINE_RE.match(stripped)
            if m_bold and _PLAIN_CHAPTER_LINE_RE.match(m_bold.group(1).strip()):
                chapter_text = m_bold.group(1).strip()
            elif _PLAIN_CHAPTER_LINE_RE.match(stripped):
                chapter_text = stripped

        if chapter_text is not None:
            normalized_chapter, consumed = _chapter_text_with_subtitle(chapter_text, src_lines, i)
            consumed_lines.update(consumed)
            if out and out[-1].strip():
                out.append("")
            out.append(f"# {normalized_chapter}")
            i += 1
            continue

        out.append(line)
        i += 1

    # Keep only one occurrence per chapter key, but preserve the richest title variant.
    # If we saw "Chapter 7" first and later "Chapter 7: The Stapletons...", replace
    # the earlier heading text in-place with the richer one.
    deduped: list[str] = []
    seen_index: dict[tuple[str, str | int], int] = {}
    seen_tail_len: dict[tuple[str, str | int], int] = {}
    for line in out:
        stripped = line.strip()
        m = _CHAPTER_MD_LINE_RE.match(stripped)
        if m:
            key = _chapter_key(
                f"{m.group(1)} {m.group(2)}{m.group(3)}"
            )
            tail_len = len((m.group(3) or "").strip())
            if key and key in seen_index:
                prev_idx = seen_index[key]
                if tail_len > seen_tail_len.get(key, 0):
                    deduped[prev_idx] = line
                    seen_tail_len[key] = tail_len
                continue
            if key:
                seen_index[key] = len(deduped)
                seen_tail_len[key] = tail_len
        deduped.append(line)

    return "\n".join(deduped).strip() + "\n"


def _demote_pre_chapter_headings(md_text: str) -> str:
    """
    In miolo, keep TOC focused on chapters:
    demote non-chapter headings that appear before first chapter heading.
    """
    out: list[str] = []
    seen_first_chapter = False
    for raw in md_text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if _CHAPTER_MD_LINE_RE.match(stripped):
            seen_first_chapter = True
            out.append(line)
            continue
        if not seen_first_chapter:
            m = re.match(r"^\s*#{1,6}\s+(.+)$", stripped)
            if m and not _MANUAL_TOC_HEADING_RE.match(stripped):
                out.append(m.group(1).strip())
                continue
        out.append(line)
    return "\n".join(out).strip() + "\n"


def _detect_epub_heading_level(md_text: str) -> int:
    """
    Detect preferred chapter heading level for TOC/split.
    Fallback is level 2.
    """
    levels: list[int] = []
    for line in md_text.splitlines():
        m = _CHAPTER_HEADING_RE.match(line.strip())
        if not m:
            continue
        levels.append(len(m.group(1)))
    if not levels:
        return 2
    return max(1, min(4, min(levels)))


def _miolo_candidates(edition: Edition) -> list[Path]:
    lang = edition.language.code
    build_dir = builds_dir(edition)
    return [
        # Current pipeline outputs.
        build_dir / f"BOOK.PRE_EDITION.{lang}.md",
        build_dir / "BOOK.PRE_EDITION.md",
        build_dir / f"BOOK.PRE_QA.{lang}.md",
        build_dir / "BOOK.PRE_QA.md",
        build_dir / "BOOK.MD_FINAL",
        build_dir / "MIOL_TERM.v1.md",
        build_dir / "miolo.md",
        # Legacy published miolo path.
        translated_miolo_path(edition),
        # Last-resort textual sources (still better than failing hard).
        build_dir / f"merge_polish_{lang}.txt",
        build_dir / "merge_polish.txt",
        build_dir / f"merge_refine_{lang}.txt",
        build_dir / "merge_refine.txt",
        build_dir / f"merge_translate_{lang}.txt",
        build_dir / "merge_translate.txt",
    ]


def resolve_miolo_source_path(edition: Edition) -> Path:
    for candidate in _miolo_candidates(edition):
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    candidates = "\n".join(f"- {p}" for p in _miolo_candidates(edition))
    raise FileNotFoundError(
        "Miolo traduzido nao encontrado. Nenhuma fonte de miolo disponivel.\n"
        f"Candidatos verificados:\n{candidates}"
    )


def _publish_legacy_miolo_snapshot(edition: Edition, source_path: Path) -> Path:
    """
    Keep legacy path populated so old flows/scripts that still read
    data/translated/<book>/<lang>/miolo.md remain functional.
    """
    target = translated_miolo_path(edition)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source_path.resolve() != target.resolve():
        shutil.copy2(source_path, target)
    return target


def _ensure_miolo_headings(text: str, language: str) -> str:
    """
    Guarantee chapter headings for EPUB TOC generation.
    Falls back to original text if heading normalization is unavailable.
    """
    try:
        from pipeline.services.miolo_transform import ensure_markdown_headings

        normalized = ensure_markdown_headings(text, language)
        return normalized if normalized else text
    except Exception:
        return text


def _ensure_epub_css(builds_base: Path) -> Path:
    css_path = builds_base / "epub.css"
    css_path.write_text(
        (
            "body { margin: 0 4%; }\n"
            "p { text-indent: 0 !important; margin: 0 0 0.9em 0; }\n"
            "img { display: block; margin: 1.2em auto !important; max-width: 100%; height: auto; }\n"
            "p > img:only-child { display: block; margin: 1.2em auto !important; }\n"
            "figure, .figure { margin: 1.2em auto; text-align: center; }\n"
            "figure img, .figure img { margin: 0 auto !important; }\n"
            "h1, h2, h3, h4, h5, h6 { text-indent: 0 !important; }\n"
        ),
        encoding="utf-8",
    )
    return css_path


def build_merged_kdp_source(edition: Edition) -> Path:
    fm_base = frontmatter_dir(edition)
    builds_base = builds_dir(edition)
    builds_base.mkdir(parents=True, exist_ok=True)

    sections: list[str] = []
    for name in ["frontispiece", "copyright", "about_edition", "about_contributor"]:
        path = fm_base / f"{name}.md"
        if path.exists():
            txt = _normalize_pagebreaks(path.read_text(encoding="utf-8").rstrip())
            if name == "frontispiece":
                txt = _remove_unwanted_taglines(txt).rstrip()
            sections.append(txt + "\n\n")

    miolo_path = resolve_miolo_source_path(edition)
    _publish_legacy_miolo_snapshot(edition, miolo_path)

    miolo_txt = miolo_path.read_text(encoding="utf-8").strip()
    miolo_txt = _ensure_miolo_headings(miolo_txt, edition.language.code).strip()
    miolo_txt = _remove_unwanted_taglines(miolo_txt).strip()
    miolo_txt = _remove_manual_contents_block(miolo_txt).strip()
    miolo_txt = _normalize_chapter_headings(miolo_txt).strip()
    miolo_txt = _demote_pre_chapter_headings(miolo_txt).strip()
    miolo_txt = _remove_unwanted_taglines(miolo_txt).strip()
    miolo_txt, cleanup_matches = _clean_leaked_body_markers(miolo_txt)
    miolo_txt, image_alt_matches = _clean_technical_image_alt_markers(miolo_txt)
    cleanup_matches.extend(image_alt_matches)
    _write_marker_cleanup_report(builds_base, cleanup_matches)
    merged_txt = "".join(sections) + "\n\n" + miolo_txt + "\n"
    _repair_missing_referenced_assets(edition, builds_base, merged_txt)

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
    merged_text = merged_path.read_text(encoding="utf-8", errors="ignore")
    heading_level = _detect_epub_heading_level(merged_text)
    toc_depth = max(2, heading_level)

    epub_path = builds_base / epub_filename

    title = (edition.title or "").strip() or "Die Abenteuer des Sherlock Holmes"
    lang = edition.language.code
    subtitle = (getattr(edition, "subtitle", "") or "").strip()
    css_path = _ensure_epub_css(builds_base)

    cmd = [
        "pandoc",
        str(merged_path),
        f"--resource-path={str(builds_base)}",
        f"--css={str(css_path)}",
        "--toc",
        f"--toc-depth={toc_depth}",
        f"--split-level={heading_level}",
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
