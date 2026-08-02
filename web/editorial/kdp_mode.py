from __future__ import annotations

import json
import html
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from editorial.frontmatter import build_frontmatter_files
from editorial.edition_renderer import EditionRenderer
from editorial.models import Edition
from gaiden.infrastructure import storage


def builds_dir(edition: Edition) -> Path:
    return storage.builds_dir(edition.work.code, edition.language.code)


def frontmatter_dir(edition: Edition) -> Path:
    return storage.frontmatter_dir(edition.work.code, edition.language.code)


def translated_miolo_path(edition: Edition) -> Path:
    return storage.translated_dir(edition.work.code, edition.language.code) / "miolo.md"


_PAGEBREAK_RE = re.compile(r"^:::\s*pagebreak\s*$", re.MULTILINE)
_LATEX_PAGEBREAK_RE = re.compile(r"\n*\\newpage\n*")
_IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\(assets/images/([^)]+)\)")
_CH_SLOT_RE = re.compile(r"ch(\d{2})_(\d{2})", re.IGNORECASE)
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_CHAPTER_HEADING_RE = re.compile(
    r"^(#{1,6})\s*(chapter|book|adventure|cap[ií]tulo|chapitre|capitolo|kapitel|livre)\b",
    re.IGNORECASE,
)
_NUMERIC_CHAPTER_HEADING_RE = re.compile(
    r"^(#{1,6})\s*([ivxlcdm]+|\d+)\s+(.+)$",
    re.IGNORECASE,
)
_MANUAL_TOC_HEADING_RE = re.compile(
    r"^\s*#{1,6}\s*(contents|table of contents|indice|índice)\s*$",
    re.IGNORECASE,
)
_PLAIN_CHAPTER_LINE_RE = re.compile(
    r"^\s*(chapter|book|adventure|cap[ií]tulo|chapitre|capitolo|kapitel|livre)\s+([ivxlcdm]+|\d+)\b(.*)$",
    re.IGNORECASE,
)
_PLAIN_NUMERIC_CHAPTER_LINE_RE = re.compile(
    r"^\s*([ivxlcdm]+|\d+)\s+(.+)$",
    re.IGNORECASE,
)
_CHAPTER_MD_LINE_RE = re.compile(
    r"^\s*#{1,6}\s*(chapter|book|adventure|cap[ií]tulo|chapitre|capitolo|kapitel|livre)\s+([ivxlcdm]+|\d+)\b(.*)$",
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
_PLAIN_TOC_LINE_RE = re.compile(r"^\s*(?:[IVXLCDM]+|\d+)\s+[A-Z].+$", re.IGNORECASE)
_VISUAL_CHAPTER_TITLE_RE = re.compile(r"^\s*\*\*.+\*\*\s*$", re.IGNORECASE)
_EXPLICIT_PRELUDE_HEADINGS = {
    "preface": "Preface",
}
_SUPERSCRIPT_MARKER_RE = re.compile(r"([ᴳᴺᴾᴰᶜ])([⁰¹²³⁴⁵⁶⁷⁸⁹]{2})")
_SUPERSCRIPT_PREFIX_TO_ID = {"ᴳ": "G", "ᴺ": "N", "ᴾ": "P", "ᴰ": "D", "ᶜ": "C"}
BOOK_0029_EDITORIAL_TITLES = {
    1: "Happiness and the Human Good",
    2: "Moral Virtue and Habit",
    3: "Choice, Responsibility, Courage, and Temperance",
    4: "The Virtues of Character",
    5: "Justice",
    6: "Practical Wisdom and Intellectual Virtue",
    7: "Self-Control, Weakness, and Pleasure",
    8: "Friendship: Its Kinds and Foundations",
    9: "Friendship and the Good Life",
    10: "Pleasure, Contemplation, and Final Happiness",
}
BOOK_0031_FR_EDITORIAL_TITLES = {
    1: "La citadelle intérieure",
    2: "Désir, aversion et perception",
    3: "Discipline et pratique quotidienne",
    4: "Conduite, vertu et société",
    5: "Maîtrise, liberté et raison",
}
BOOK_0031_FR_GLOSSAIRE_ANCHORS = {
    "Arrien de Nicomédie": "glossaire-arrien-de-nicomedie",
    "Assentiment": "glossaire-assentiment",
    "Chrysippe": "glossaire-chrysippe",
    "Citadelle intérieure": "glossaire-citadelle-interieure",
    "Cyniques": "glossaire-cyniques",
    "Diogène": "glossaire-diogene",
    "Domitien": "glossaire-domitien",
    "Enchiridion": "glossaire-enchiridion",
    "Épaphrodite": "glossaire-epaphrodite",
    "Épictète de Hiérapolis": "glossaire-epictete-de-hierapolis",
    "Faculté directrice": "glossaire-faculte-directrice",
    "Hiérapolis": "glossaire-hierapolis",
    "Marc Aurèle": "glossaire-marc-aurele",
    "Musonius Rufus": "glossaire-musonius-rufus",
    "Nature": "glossaire-nature",
    "Nicopolis": "glossaire-nicopolis",
    "Prohairesis": "glossaire-prohairesis",
    "Représentation": "glossaire-representation",
    "Socrate": "glossaire-socrate",
    "Stoïcisme": "glossaire-stoicisme",
    "Thérapie cognitivo-comportementale": "glossaire-therapie-cognitivo-comportementale",
    "Volonté": "glossaire-volonte",
    "Zénon de Citium": "glossaire-zenon-de-citium",
}
BOOK_0031_FR_GLOSSAIRE_LINKS = [
    (("Arrien de Nicomédie",), "glossaire-arrien-de-nicomedie"),
    (("Musonius Rufus",), "glossaire-musonius-rufus"),
    (("Socrate",), "glossaire-socrate"),
    (("cyniques", "Cyniques"), "glossaire-cyniques"),
    (("Diogène",), "glossaire-diogene"),
    (("stoïcisme", "Stoïcisme"), "glossaire-stoicisme"),
    (("prohairesis", "Prohairesis"), "glossaire-prohairesis"),
    (("assentiment", "Assentiment"), "glossaire-assentiment"),
    (("représentations", "représentation", "Représentation"), "glossaire-representation"),
    (("faculté directrice", "Faculté directrice"), "glossaire-faculte-directrice"),
    (("Chrysippe",), "glossaire-chrysippe"),
    (("Zénon de Citium",), "glossaire-zenon-de-citium"),
    (("Marc Aurèle",), "glossaire-marc-aurele"),
    (("thérapie cognitivo-comportementale", "Thérapie cognitivo-comportementale"), "glossaire-therapie-cognitivo-comportementale"),
    (("Nicopolis",), "glossaire-nicopolis"),
    (("Hiérapolis",), "glossaire-hierapolis"),
    (("Enchiridion",), "glossaire-enchiridion"),
    (("Épaphrodite",), "glossaire-epaphrodite"),
    (("Épictète de Hiérapolis",), "glossaire-epictete-de-hierapolis"),
    (("Domitien",), "glossaire-domitien"),
    (("nature", "Nature"), "glossaire-nature"),
    (("volonté", "Volonté"), "glossaire-volonte"),
]
_SUPERSCRIPT_DIGITS_TO_ASCII = {
    "⁰": "0",
    "¹": "1",
    "²": "2",
    "³": "3",
    "⁴": "4",
    "⁵": "5",
    "⁶": "6",
    "⁷": "7",
    "⁸": "8",
    "⁹": "9",
}


def _resolve_cover_path(edition: Edition) -> Path | None:
    cover_value = (getattr(edition, "cover_filepath", "") or "").strip()
    if cover_value:
        cover_path = storage.resolve_storage_path(cover_value)
        if cover_path.exists():
            return cover_path

    cover_dir = storage.covers_dir(edition.work.code, edition.language.code)
    for name in ("cover.jpg", "cover.png"):
        candidate = cover_dir / name
        if candidate.exists():
            return candidate
    return None


def _normalize_pagebreaks(text: str) -> str:
    normalized = _PAGEBREAK_RE.sub("::: pagebreak\n:::", text)
    return _LATEX_PAGEBREAK_RE.sub("\n\n\\\\newpage\n\n", normalized).strip() + "\n"


def _split_core_and_supplements(text: str) -> tuple[str, str]:
    markers = [
        "\n## LETTERS TO FRONTO",
        "\n# LETTERS TO FRONTO",
        "\n## GLOSSARY",
        "\n# GLOSSARY",
        "\n## GLOSSAIRE",
        "\n# GLOSSAIRE",
        "\nGLOSSAIRE",
    ]
    indices = [text.find(marker) for marker in markers if text.find(marker) != -1]
    if not indices:
        return text, ""
    cut = min(indices)
    return text[:cut].rstrip(), text[cut:].lstrip()


def _is_glossary_heading(stripped: str) -> bool:
    return stripped.casefold() in {"# glossary", "# glossaire"}


def _promote_supplement_headings(text: str) -> str:
    if not text.strip():
        return text
    text = re.sub(r"(?m)^##\s+LETTERS TO FRONTO\s*$", "# LETTERS TO FRONTO", text)
    text = re.sub(r"(?m)^##\s+GLOSSARY\s*$", "# GLOSSARY", text)
    text = re.sub(r"(?m)^(?:##\s+)?GLOSSAIRE\s*$", "# GLOSSAIRE", text)
    return text.strip() + "\n"


def _clean_supplement_false_headings(text: str) -> str:
    if not text.strip():
        return text
    text = re.sub(
        r"(?m)(writes to)\s*\n\s*\\newpage\s*\n\s*#\s+Chapter\s+\d+\s*-\s*Fronto as follows:---\s*$",
        r"\1 Fronto as follows:---",
        text,
    )
    text = re.sub(
        r"(?m)^\s*#\s+Chapter\s+\d+\s*-\s*Fronto as follows:---\s*$",
        "Fronto as follows:---",
        text,
    )
    return text.strip() + "\n"


def _bold_glossary_headwords(text: str) -> str:
    if not text.strip() or not re.search(r"(?m)^#\s+GLOSS(?:ARY|AIRE)\s*$", text):
        return text

    lines = text.splitlines()
    out: list[str] = []
    in_glossary = False
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if _is_glossary_heading(stripped):
            in_glossary = True
            out.append(line)
            continue
        if in_glossary and stripped.startswith("# ") and not _is_glossary_heading(stripped):
            in_glossary = False
        if not in_glossary or not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        if re.match(r"^(?:<span id=\"glossary-term-\d+\"></span>)?[GTV]\d{3}\s+-\s+", stripped):
            out.append(line)
            continue
        if stripped.startswith("**"):
            out.append(line)
            continue

        m_colon = re.match(r"^([A-ZÆŒΆ-῾][^:]{0,90}?):\s+(.*)$", stripped)
        if m_colon:
            out.append(f"**{m_colon.group(1).strip()}** - {m_colon.group(2).strip()}")
            continue
        m_comma = re.match(r"^([A-ZÆŒΆ-῾][^,]{0,90}?),(?:\s+)(.*)$", stripped)
        if m_comma:
            head = m_comma.group(1).strip()
            if head.lower().startswith("both names"):
                out.append(line)
            else:
                out.append(f"**{head}** - {m_comma.group(2).strip()}")
            continue
        out.append(line)
    return "\n".join(out).strip() + "\n"


def _inline_glossary_continuations(text: str) -> str:
    if not text.strip() or not re.search(r"(?m)^#\s+GLOSS(?:ARY|AIRE)\s*$", text):
        return text

    lines = text.splitlines()
    out: list[str] = []
    in_glossary = False
    intro_seen = False

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if _is_glossary_heading(stripped):
            in_glossary = True
            intro_seen = False
            out.append(line)
            continue

        if in_glossary and stripped.startswith("# ") and not _is_glossary_heading(stripped):
            in_glossary = False

        if not in_glossary:
            out.append(line)
            continue

        if not stripped:
            out.append(line)
            continue

        if not intro_seen:
            out.append(line)
            intro_seen = True
            continue

        is_headword = stripped.startswith("**")
        if is_headword:
            out.append(line)
            continue

        if out:
            j = len(out) - 1
            while j >= 0 and not out[j].strip():
                j -= 1
            if j >= 0 and out[j].strip().startswith("**"):
                out[j] = f"{out[j].rstrip()} {stripped}"
                continue

        out.append(line)

    return "\n".join(out).strip() + "\n"


def _normalize_glossary_inline_format(text: str) -> str:
    if not text.strip() or not re.search(r"(?m)^#\s+GLOSS(?:ARY|AIRE)\s*$", text):
        return text
    text = re.sub(r"(?m)^(\*\*.+?\*\*)\s*[,:\u2014-]\s+", r"\1&nbsp;-&nbsp;", text)
    return text


def _format_glossary_as_ordered_list(text: str) -> str:
    if not text.strip() or not re.search(r"(?m)^#\s+GLOSS(?:ARY|AIRE)\s*$", text):
        return text

    lines = text.splitlines()
    out: list[str] = []
    in_glossary = False
    current_entry: str | None = None

    def flush_entry() -> None:
        nonlocal current_entry
        if current_entry:
            out.append(current_entry.strip())
            out.append("")
            current_entry = None

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if _is_glossary_heading(stripped):
            in_glossary = True
            out.append(line)
            out.append("")
            continue

        if in_glossary and stripped.startswith("# ") and not _is_glossary_heading(stripped):
            flush_entry()
            in_glossary = False
            out.append(line)
            continue

        if not in_glossary:
            out.append(line)
            continue

        if not stripped:
            continue

        if re.match(r"^\d+\.\s+\S", stripped):
            flush_entry()
            out.append(re.sub(r"^(\d+)\.", r"\1\\.", stripped))
            out.append("")
            continue

        if re.fullmatch(r"[GTV]\d{3}", stripped):
            flush_entry()
            current_entry = stripped
            continue

        if current_entry and re.fullmatch(r"[GTV]\d{3}", current_entry):
            current_entry = f"{current_entry} - {stripped}"
            continue

        if stripped.startswith("**"):
            if current_entry:
                entries.append(current_entry.strip())
            current_entry = stripped
            continue

        if current_entry:
            separator = " - " if re.match(r"^[GTV]\d{3}\b", current_entry) and current_entry.count(" - ") == 1 else " "
            current_entry = f"{current_entry}{separator}{stripped}"
            continue

        flush_entry()
        out.append(stripped)
        out.append("")

    if in_glossary:
        flush_entry()

    return "\n".join(out).strip() + "\n"


def _extract_glossary_entries(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        m = re.match(r"^(?:<span id=\"glossary-term-\d+\"></span>)?[GTV]\d{3}\s+-\s+(.+?)\s+-\s+", stripped)
        if not m:
            m = re.match(r"^\s*1\.\s+\*\*(.+?)\*\*", stripped)
        if not m:
            m = re.match(r"^(?:<span id=\"glossary-term-\d+\"></span>)?\*\*(.+?)\*\*", stripped)
        if not m:
            continue
        term = html.unescape(m.group(1).replace("&nbsp;", " ")).strip()
        entry_id = f"glossary-term-{idx:03d}"
        aliases = [term]
        if " (" in term:
            aliases.append(term.split(" (", 1)[0].strip())
        if term.isupper():
            aliases.append(term.title())
        seen: set[str] = set()
        unique_aliases: list[str] = []
        for alias in aliases:
            cleaned = alias.strip()
            if not cleaned:
                continue
            key = cleaned.casefold()
            if key in seen:
                continue
            seen.add(key)
            unique_aliases.append(cleaned)
        entries.append({"term": term, "id": entry_id, "aliases": unique_aliases})
    return entries


def _inject_glossary_ids(text: str, entries: list[dict[str, str]]) -> str:
    if not text.strip() or not entries:
        return text

    lines = text.splitlines()
    entry_iter = iter(entries)
    current = next(entry_iter, None)
    out: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        if current:
            m = re.match(r"^(\s*)((?:[GTV]\d{3}\s+-\s+.+?)|(?:\*\*.+?\*\*.*)|(?:1\.\s+\*\*.+?\*\*.*))$", line)
            if m:
                out.append(f'{m.group(1)}<span id="{current["id"]}"></span>{m.group(2)}')
                current = next(entry_iter, None)
                continue
        out.append(line)
    return "\n".join(out).strip() + "\n"


def _annotate_first_glossary_mentions(text: str, entries: list[dict[str, str]]) -> str:
    if not text.strip() or not entries:
        return text

    annotated = text
    for idx, entry in enumerate(entries, start=1):
        replacement_done = False
        for alias in sorted(entry["aliases"], key=len, reverse=True):
            if len(alias) < 3:
                continue
            pattern = re.compile(
                rf"(?<![\w>])({re.escape(alias)})(?![\w<])"
            )

            def _repl(match: re.Match[str]) -> str:
                nonlocal replacement_done
                if replacement_done:
                    return match.group(0)
                replacement_done = True
                term = match.group(1)
                return f'{term}<sup><a href="#{entry["id"]}">{idx}</a></sup>'

            annotated, count = pattern.subn(_repl, annotated, count=1)
            if replacement_done or count:
                break
    return annotated


def _collapse_orphan_numbered_paragraphs(text: str) -> str:
    """
    Join cases like:
        12.

        Paragraph...
    into:
        12. Paragraph...
    This keeps aphorism numbering readable in EPUB output.
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        current = lines[i].rstrip()
        stripped = current.strip()
        if re.fullmatch(r"\d+\.", stripped):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                nxt = lines[j].strip()
                if nxt and not nxt.startswith("#") and not _IMAGE_LINE_RE.match(nxt):
                    out.append(f"{stripped} {nxt}")
                    i = j + 1
                    continue
        out.append(current)
        i += 1
    return "\n".join(out).strip() + "\n"


def _renumber_core_aphorisms(md_text: str) -> str:
    def capitalize_first_alpha(text: str) -> str:
        for idx, char in enumerate(text):
            if char.isalpha():
                return text[:idx] + char.upper() + text[idx + 1 :]
        return text

    def append_blocks(target: list[str], blocks: list[str]) -> None:
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                continue
            target.append(stripped)
            target.append("")

    def flush_item(target: list[str], number: int | None, blocks: list[str]) -> None:
        if number is None or not blocks:
            return
        first = blocks[0].strip()
        if not first:
            return
        target.append(f"{number}. {first}")
        for extra in blocks[1:]:
            cleaned = extra.strip()
            if not cleaned:
                continue
            target.append("")
            for line in cleaned.splitlines():
                target.append(f"    {line.rstrip()}")
        target.append("")

    sections = re.split(r"(?m)(?=^#\s+(?:Chapter|Book|Adventure|Cap[ií]tulo|Kapitel|Chapitre|Livre)\b)", md_text.strip())
    rebuilt: list[str] = []

    for section in sections:
        if not section.strip():
            continue
        lines = section.strip().splitlines()
        heading = lines[0].rstrip()
        body = "\n".join(lines[1:]).strip()
        rebuilt.append(heading)
        if not body:
            rebuilt.append("")
            continue

        rebuilt.append("")
        blocks = re.split(r"\n\s*\n", body)
        prelude: list[str] = []
        item_blocks: list[str] = []
        current_number: int | None = None
        next_number = 1
        in_items = False

        for block in blocks:
            stripped = block.strip()
            if not stripped:
                continue
            m = re.match(r"^(\d+)([.)])\s*(.*)$", stripped, re.DOTALL)
            dash = re.match(r"^(\d{1,3}\s+[—–-])\s*(.*)$", stripped, re.DOTALL)
            if m or dash:
                if current_number is None and prelude:
                    append_blocks(rebuilt, prelude)
                    prelude = []
                flush_item(rebuilt, current_number, item_blocks)
                if dash:
                    first_block = capitalize_first_alpha((dash.group(2) or "").strip())
                    rebuilt.append(f"**{dash.group(1)}** {first_block}".rstrip())
                    rebuilt.append("")
                    current_number = None
                    item_blocks = []
                    in_items = True
                    continue
                current_number = next_number
                next_number += 1
                item_blocks = []
                first_block = capitalize_first_alpha((m.group(3) or "").strip())
                if first_block:
                    item_blocks.append(first_block)
                in_items = True
                continue

            if in_items and current_number is not None:
                item_blocks.append(stripped)
            else:
                prelude.append(stripped)

        if current_number is None and prelude:
            append_blocks(rebuilt, prelude)
        else:
            flush_item(rebuilt, current_number, item_blocks)
            if rebuilt and rebuilt[-1] != "":
                rebuilt.append("")

    return "\n".join(rebuilt).strip() + "\n"


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
        storage.images_dir(book, lang),
        storage.images_dir(book, lang) / "consolidated",
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


def _glossary_candidate_paths(edition: Edition, builds_base: Path) -> list[Path]:
    book_code = edition.work.code
    lang = edition.language.code
    return [
        builds_base / "glossary" / "glossary.json",
        builds_base / "glossary" / f"{book_code}_{lang}_glossary.json",
        builds_base / "glossary" / f"{book_code}_glossary.json",
        builds_base / "glossary" / f"{book_code}_nicomachean_ethics_glossary_FINAL.json",
        builds_base / f"{book_code}_glossary.json",
        storage.data_dir() / "glossaries" / book_code / lang / "glossary.json",
    ]


def _load_external_glossary_entries(edition: Edition, builds_base: Path) -> list[dict]:
    for path in _glossary_candidate_paths(edition, builds_base):
        if not path.exists() or path.stat().st_size == 0:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if isinstance(entries, list):
            return [entry for entry in entries if isinstance(entry, dict) and entry.get("id")]
    return []


def _superscript_marker_to_glossary_id(prefix: str, digits: str) -> str:
    ascii_digits = "".join(_SUPERSCRIPT_DIGITS_TO_ASCII[digit] for digit in digits)
    return f"{_SUPERSCRIPT_PREFIX_TO_ID[prefix]}{ascii_digits}"


def _link_external_glossary_markers(text: str, entries: list[dict]) -> str:
    entry_ids = {str(entry.get("id") or "").upper() for entry in entries}

    def replace(match: re.Match[str]) -> str:
        glossary_id = _superscript_marker_to_glossary_id(match.group(1), match.group(2))
        marker = match.group(0)
        if glossary_id.upper() not in entry_ids:
            return marker
        return f"[{marker}](#glossary-{glossary_id.lower()})"

    return _SUPERSCRIPT_MARKER_RE.sub(replace, text)


def _external_glossary_group_title(category: str) -> str:
    if category in {"greek_term", "greek_phrase"}:
        return "Greek Terms"
    if category in {"city", "people_or_region"}:
        return "Places and Peoples"
    if category in {"god", "divine_figure"}:
        return "Gods and Divine Figures"
    if category in {"technical_concept"}:
        return "Technical Concepts"
    return "Names and Historical References"


def _build_external_glossary_markdown(entries: list[dict]) -> str:
    if not entries:
        return ""
    groups: dict[str, list[dict]] = {}
    for entry in entries:
        groups.setdefault(_external_glossary_group_title(str(entry.get("category") or "")), []).append(entry)

    lines = ["# Glossary"]
    group_order = [
        "Greek Terms",
        "Names and Historical References",
        "Places and Peoples",
        "Gods and Divine Figures",
        "Technical Concepts",
    ]
    for group in group_order:
        group_entries = groups.get(group) or []
        if not group_entries:
            continue
        lines.extend(["", f"## {group}"])
        for entry in group_entries:
            entry_id = str(entry.get("id") or "").upper()
            anchor = f"glossary-{entry_id.lower()}"
            display = str(entry.get("display") or entry.get("name") or entry.get("english") or entry_id).strip()
            lines.extend(["", f"### [{entry_id}] {display} {{#{anchor}}}"])
            if entry.get("greek"):
                lines.append(f"**Greek:** {entry['greek']}  ")
            if entry.get("transliteration"):
                lines.append(f"**Transliteration:** {entry['transliteration']}  ")
            if entry.get("english"):
                lines.append(f"**Meaning:** {entry['english']}.  ")
            if entry.get("note"):
                lines.append(f"**Note:** {entry['note']}")
    return "\n".join(lines).strip() + "\n"


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


def _normalize_title_token(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", (text or "").upper())


def _strip_leading_center_block(lines: list[str]) -> list[str]:
    if not lines:
        return lines
    if lines[0].strip() != "::: center":
        return lines
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == ":::":
            end = idx
            break
    if end is None:
        return lines
    return lines[end + 1 :]


def _looks_like_legacy_boilerplate(line: str, edition: Edition) -> bool:
    stripped = line.strip()
    if not stripped:
        return True

    upper = stripped.upper()
    title_token = _normalize_title_token(getattr(edition, "title", "") or "")
    author_name = ""
    author = getattr(getattr(edition, "work", None), "author", None)
    if author is not None:
        author_name = getattr(author, "name", "") or ""
    author_token = _normalize_title_token(author_name)
    line_token = _normalize_title_token(stripped)

    if title_token and line_token == title_token:
        return True
    if author_token and line_token == author_token:
        return True
    if author_token and line_token == f"BY{author_token}":
        return True
    if upper.startswith("FIRST PUBLISHED"):
        return True
    if upper == "CONTENTS" or upper == "TABLE OF CONTENTS":
        return True
    if stripped in {"by", "BY"}:
        return True
    if len(stripped.split()) <= 10 and stripped == upper:
        return True
    if len(stripped) <= 80 and stripped == upper and not any(ch in stripped for ch in ".!?"):
        return True
    return False


def _strip_legacy_contents_block(lines: list[str]) -> list[str]:
    start = None
    for idx, raw in enumerate(lines[:120]):
        upper = raw.strip().upper()
        if upper in {"CONTENTS", "TABLE OF CONTENTS"}:
            start = idx
            break
    if start is None:
        return lines

    end = start + 1
    while end < len(lines):
        stripped = lines[end].strip()
        if not stripped:
            end += 1
            continue
        if stripped == r"\newpage" or _CHAPTER_MD_LINE_RE.match(stripped):
            break
        if _PLAIN_TOC_LINE_RE.match(stripped):
            end += 1
            continue
        break
    return lines[:start] + lines[end:]


def _looks_like_paragraph(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    words = stripped.split()
    if len(words) < 8:
        return False
    return any(ch in stripped for ch in {".", ",", ";", ":", "?", "!"})


def _match_plain_numeric_heading(line: str) -> re.Match[str] | None:
    stripped = line.strip()
    match = _PLAIN_NUMERIC_CHAPTER_LINE_RE.match(stripped)
    if not match:
        return None

    title = (match.group(2) or "").strip()
    if not title:
        return None
    if _looks_like_paragraph(title):
        return None
    if len(title) > 120 or len(title.split()) > 16:
        return None
    if any(ch in title for ch in ".?!"):
        return None

    first_alpha = next((ch for ch in title if ch.isalpha()), "")
    if first_alpha and not first_alpha.isupper():
        return None
    return match


def _prune_prelude_boilerplate(lines: list[str], edition: Edition) -> list[str]:
    pruned: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped == r"\newpage":
            continue
        if stripped and not _looks_like_paragraph(raw) and _looks_like_legacy_boilerplate(raw, edition):
            continue
        pruned.append(raw)
    return pruned


def _extract_explicit_prelude_heading(lines: list[str]) -> tuple[str | None, list[str]]:
    if not lines:
        return None, lines

    first = re.sub(r"^\s*#{1,6}\s*", "", lines[0].strip()).rstrip(":").strip()
    heading = _EXPLICIT_PRELUDE_HEADINGS.get(first.casefold())
    if not heading:
        return None, lines

    remainder = lines[1:]
    while remainder and not remainder[0].strip():
        remainder.pop(0)
    return heading, remainder


def _normalize_pre_chapter_prelude(md_text: str, edition: Edition) -> str:
    lines = md_text.splitlines()
    first_chapter = None
    for idx, raw in enumerate(lines):
        if _CHAPTER_MD_LINE_RE.match(raw.strip()):
            first_chapter = idx
            break
    if first_chapter is None:
        return md_text

    prelude = lines[:first_chapter]
    chapters = lines[first_chapter:]

    while prelude and not prelude[0].strip():
        prelude.pop(0)
    prelude = _strip_leading_center_block(prelude)
    while prelude and not prelude[0].strip():
        prelude.pop(0)

    cutoff = 0
    while cutoff < len(prelude):
        current = prelude[cutoff]
        if _looks_like_paragraph(current):
            break
        if _looks_like_legacy_boilerplate(current, edition):
            cutoff += 1
            continue
        break
    prelude = prelude[cutoff:]
    prelude = _strip_legacy_contents_block(prelude)
    prelude = _prune_prelude_boilerplate(prelude, edition)

    while prelude and not prelude[0].strip():
        prelude.pop(0)
    while prelude and not prelude[-1].strip():
        prelude.pop()

    explicit_heading, prelude = _extract_explicit_prelude_heading(prelude)

    if not prelude:
        return "\n".join(chapters).strip() + "\n"

    if edition.work.code == "book_0030":
        return "\n".join(chapters).strip() + "\n"

    if prelude and any(_looks_like_paragraph(line) for line in prelude) and not any(line.strip().startswith("#") for line in prelude):
        prelude = [f"# {explicit_heading or 'Preface'}", ""] + prelude

    merged = prelude + [""] + chapters
    return "\n".join(merged).strip() + "\n"


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


def _assert_short_structural_headings(md_text: str, limit: int = 60) -> None:
    for line_no, raw in enumerate(md_text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped.startswith("#"):
            continue
        heading = re.sub(r"^#{1,6}\s+", "", stripped).strip()
        heading_limit = 80 if re.match(r"^Book\s+\d{2}\s+—\s+", heading) else limit
        if len(heading) <= heading_limit:
            continue
        raise RuntimeError(
            f"EPUB/PDF heading validation failed: heading longer than {heading_limit} characters "
            f"at line {line_no}: {heading}"
        )


def _apply_book_0029_editorial_titles(md_text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        hashes = match.group(1)
        book_num = int(match.group(2))
        title = BOOK_0029_EDITORIAL_TITLES.get(book_num)
        if not title:
            return match.group(0)
        return f"{hashes} Book {book_num:02d} — {title}"

    return re.sub(
        r"(?m)^(#{1,6})\s*Book\s+(\d{1,2})\b(?:\s*[.:—-].*)?$",
        replace,
        md_text,
    )


def _book_0029_title_report() -> dict[str, str]:
    return {f"Book {num:02d}": title for num, title in BOOK_0029_EDITORIAL_TITLES.items()}


def _assert_book_0029_editorial_titles(md_text: str) -> None:
    found: list[tuple[int, str]] = []
    for line_no, raw in enumerate(md_text.splitlines(), start=1):
        m = re.match(r"^#\s+Book\s+(\d{2})\s+—\s+(.+?)\s*$", raw.strip())
        if not m:
            continue
        number = int(m.group(1))
        found.append((number, m.group(2)))

    expected_numbers = list(BOOK_0029_EDITORIAL_TITLES)
    found_numbers = [number for number, _ in found]
    if found_numbers != expected_numbers:
        raise RuntimeError(
            "Book title validation failed: expected Book 01 through Book 10 in order; "
            f"found {found_numbers}"
        )

    for number, title in found:
        expected = BOOK_0029_EDITORIAL_TITLES[number]
        if title != expected:
            raise RuntimeError(
                f"Book title validation failed for Book {number:02d}: "
                f"expected {expected!r}, found {title!r}"
            )


def _assert_chapter_headings_short(md_text: str, limit: int = 30) -> None:
    for line_no, raw in enumerate(md_text.splitlines(), start=1):
        heading = re.sub(r"^#{1,6}\s+", "", raw.strip()).strip()
        if not re.match(r"^Chapter\s+\d{2}\.$", heading):
            if re.match(r"^Chapter\b", heading):
                raise RuntimeError(f"Chapter heading validation failed at line {line_no}: {heading}")
            continue
        if len(heading) > limit:
            raise RuntimeError(
                f"Chapter heading validation failed: heading longer than {limit} characters "
                f"at line {line_no}: {heading}"
            )


def _clean_leaked_body_markers(md_text: str) -> tuple[str, list[dict[str, object]]]:
    _assert_no_marker_in_headings(md_text)
    _assert_short_structural_headings(md_text)
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


def _chapter_label_for_language(language: str) -> str:
    lang = (language or "").strip().lower()
    if lang in {"de"}:
        return "Kapitel"
    if lang in {"es"}:
        return "Capítulo"
    if lang in {"ptbr", "pt-br"}:
        return "Capítulo"
    if lang in {"fr"}:
        return "Chapitre"
    if lang in {"it"}:
        return "Capitolo"
    return "Chapter"


def _normalize_chapter_headings(md_text: str, language: str = "en") -> str:
    """
    Make chapter headings explicit and stable for EPUB TOC:
    - normalize "### Chapter X" / "# Chapter X" to "# Chapter X"
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
        if m:
            prefix = m.group(1).strip().lower()
            num = m.group(2).strip()
        else:
            n = _match_plain_numeric_heading(text)
            if not n:
                return None
            prefix = "chapter"
            num = n.group(1).strip()
        if num.isdigit():
            key_num: str | int = int(num)
        else:
            parsed = _roman_to_int(num)
            key_num = parsed if parsed is not None else num.lower()
        return (prefix, key_num)

    def _chapter_text_with_body(chapter_text: str, lines: list[str], idx: int) -> tuple[str, list[str], set[int]]:
        m = _PLAIN_CHAPTER_LINE_RE.match(chapter_text.strip())
        if m:
            prefix = m.group(1).capitalize()
            num = m.group(2).strip()
            tail = (m.group(3) or "").strip().lstrip(" .:-–—")
        else:
            n = _match_plain_numeric_heading(chapter_text)
            if not n:
                return chapter_text.strip(), [], set()
            prefix = _chapter_label_for_language(language)
            num = n.group(1).strip()
            tail = (n.group(2) or "").strip().lstrip(" .:-–—")
        consumed: set[int] = set()
        body_lines: list[str] = []

        if not tail:
            # If a bold title appears soon after (optionally after image), keep it as body
            # text below the short chapter heading. EPUB/PDF TOC headings must stay short.
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
            body_lines.append(tail)
        if num.isdigit():
            return f"{prefix} {int(num):02d}.", body_lines, consumed
        parsed = _roman_to_int(num)
        if parsed is not None:
            return f"{prefix} {parsed:02d}.", body_lines, consumed
        return f"{prefix} {num}.", body_lines, consumed

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
        elif _NUMERIC_CHAPTER_HEADING_RE.match(stripped):
            chapter_text = stripped.lstrip("#").strip()
        else:
            m_bold = _BOLD_LINE_RE.match(stripped)
            if m_bold and (
                _PLAIN_CHAPTER_LINE_RE.match(m_bold.group(1).strip())
                or _match_plain_numeric_heading(m_bold.group(1).strip())
            ):
                chapter_text = m_bold.group(1).strip()
            elif _PLAIN_CHAPTER_LINE_RE.match(stripped) or _match_plain_numeric_heading(stripped):
                chapter_text = stripped

        if chapter_text is not None:
            normalized_chapter, body_lines, consumed = _chapter_text_with_body(chapter_text, src_lines, i)
            consumed_lines.update(consumed)
            if out and out[-1].strip():
                out.append("")
            heading_hashes = "#" if normalized_chapter.lower().startswith("book ") else "##"
            out.append(f"{heading_hashes} {normalized_chapter}")
            if body_lines:
                out.append("")
                out.extend(body_lines)
            i += 1
            continue

        out.append(line)
        i += 1

    # Keep only one occurrence per chapter key within the current book, but preserve
    # the richest title variant. Chapter numbers repeat across books, so the book
    # scope is part of the dedupe key.
    deduped: list[str] = []
    seen_index: dict[tuple[tuple[str, str | int] | None, tuple[str, str | int]], int] = {}
    seen_tail_len: dict[tuple[tuple[str, str | int] | None, tuple[str, str | int]], int] = {}
    current_book_key: tuple[str, str | int] | None = None
    for line in out:
        stripped = line.strip()
        m = _CHAPTER_MD_LINE_RE.match(stripped)
        if m:
            key = _chapter_key(
                f"{m.group(1)} {m.group(2)}{m.group(3)}"
            )
            if key and key[0] == "book":
                current_book_key = key
            tail_len = len((m.group(3) or "").strip())
            scoped_key = (None if key and key[0] == "book" else current_book_key, key) if key else None
            if scoped_key and scoped_key in seen_index:
                prev_idx = seen_index[scoped_key]
                if tail_len > seen_tail_len.get(scoped_key, 0):
                    deduped[prev_idx] = line
                    seen_tail_len[scoped_key] = tail_len
                continue
            if scoped_key:
                seen_index[scoped_key] = len(deduped)
                seen_tail_len[scoped_key] = tail_len
        deduped.append(line)

    return "\n".join(deduped).strip() + "\n"


def _insert_visual_chapter_titles(md_text: str) -> str:
    # Keep chapter headings authoritative and strip immediate duplicate
    # visual-title lines that may appear below a heading or chapter image.
    lines = md_text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        out.append(line)
        i += 1
        if not _CHAPTER_MD_LINE_RE.match(stripped):
            continue

        chapter_title = stripped.lstrip("#").strip()
        chapter_token = _normalize_title_token(chapter_title)

        while i < len(lines):
            probe = lines[i]
            probe_stripped = probe.strip()
            if not probe_stripped:
                out.append(probe)
                i += 1
                continue
            if _IMAGE_LINE_RE.match(probe_stripped):
                out.append(probe)
                i += 1
                continue

            bold = _BOLD_LINE_RE.match(probe_stripped)
            if bold and _normalize_title_token(bold.group(1).strip()) == chapter_token:
                i += 1
                if i < len(lines) and not lines[i].strip():
                    i += 1
            break

    return "\n".join(out).strip() + "\n"


def _apply_book_0031_fr_editorial_shape(md_text: str) -> str:
    lines = md_text.splitlines()
    out: list[str] = []
    skip_next_subtitle = False
    skipping_prelude = False

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if re.match(r"^#\s+Preface\s*$", stripped):
            skipping_prelude = True
            continue

        chapter = re.match(r"^##\s+Chapitre\s+0?(\d+)\.\s*$", stripped)
        if chapter:
            skipping_prelude = False
            number = int(chapter.group(1))
            title = BOOK_0031_FR_EDITORIAL_TITLES.get(number)
            if title:
                out.append(f"# Chapitre {number} — {title}")
                skip_next_subtitle = True
                continue

        if skipping_prelude:
            continue

        if skip_next_subtitle:
            if not stripped:
                continue
            if any(stripped.startswith(title) for title in BOOK_0031_FR_EDITORIAL_TITLES.values()):
                skip_next_subtitle = False
                continue
            skip_next_subtitle = False

        out.append(line)

    text = "\n".join(out).strip() + "\n"
    legacy_epilogue = re.search(r"(?m)^Épilogue\s*$", text)
    if legacy_epilogue:
        text = text[: legacy_epilogue.start()].rstrip() + "\n"
    return text


def _add_book_0031_fr_glossaire_anchors(md_text: str) -> str:
    text = md_text
    for term, anchor in BOOK_0031_FR_GLOSSAIRE_ANCHORS.items():
        if f'id="{anchor}"' in text:
            continue
        text = re.sub(
            rf"(?m)^(\*\*{re.escape(term)}\*\*\s*)$",
            rf'<a id="{anchor}"></a>' + "\n" + r"\1",
            text,
            count=1,
        )
    return text


def _link_book_0031_fr_glossaire_terms(md_text: str) -> str:
    if "# Glossaire" not in md_text:
        return md_text

    body, glossary = md_text.split("# Glossaire", 1)
    glossary = "# Glossaire" + glossary

    intro_start = body.find("# Introduction")
    first_chapter = body.find("# Chapitre 1")
    epilogue_start = body.find("# Épilogue")

    segments: list[tuple[str, str]] = []
    if intro_start != -1 and first_chapter != -1 and intro_start < first_chapter:
        segments.append(("intro", body[intro_start:first_chapter]))
    if epilogue_start != -1:
        segments.append(("epilogue", body[epilogue_start:]))

    linked_anchors: set[str] = set()

    def link_once(segment: str, aliases: tuple[str, ...], anchor: str) -> str:
        if anchor in linked_anchors or f"](#${anchor})" in segment or f"]({anchor})" in segment or f"](#" + anchor + ")" in segment:
            return segment
        lines = segment.splitlines()
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or f'id="{anchor}"' in line:
                continue
            if "[#" in line or re.search(r"\[[^\]]+\]\([^)]+\)", line):
                continue
            for alias in aliases:
                pattern = re.compile(rf"(?<![\w\]\-])({re.escape(alias)})(?![\w\[-])")
                if not pattern.search(line):
                    continue
                lines[idx] = pattern.sub(lambda match: f"[{match.group(1)}](#{anchor})", line, count=1)
                linked_anchors.add(anchor)
                return "\n".join(lines)
        return segment

    rebuilt_segments: dict[str, str] = {}
    for name, segment in segments:
        current = segment
        for aliases, anchor in BOOK_0031_FR_GLOSSAIRE_LINKS:
            current = link_once(current, aliases, anchor)
        rebuilt_segments[name] = current

    if "intro" in rebuilt_segments and intro_start != -1 and first_chapter != -1:
        body = body[:intro_start] + rebuilt_segments["intro"] + body[first_chapter:]
    if "epilogue" in rebuilt_segments:
        epilogue_start = body.find("# Épilogue")
        if epilogue_start != -1:
            body = body[:epilogue_start] + rebuilt_segments["epilogue"]

    merged = body.rstrip() + "\n\n" + glossary.lstrip()
    return re.sub(r":::(?=#)", ":::\n\n", merged)


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
    has_book_h1 = False
    has_chapter_h2 = False
    levels: list[int] = []
    for line in md_text.splitlines():
        m = _CHAPTER_HEADING_RE.match(line.strip())
        if not m:
            continue
        hashes = len(m.group(1))
        heading_word = m.group(2).lower()
        if heading_word == "book" and hashes == 1:
            has_book_h1 = True
        if heading_word == "chapter" and hashes == 2:
            has_chapter_h2 = True
        levels.append(hashes)
    if has_book_h1 and has_chapter_h2:
        return 2
    if not levels:
        return 2
    return max(1, min(4, min(levels)))


def _miolo_candidates(edition: Edition) -> list[Path]:
    lang = edition.language.code
    build_dir = builds_dir(edition)
    return [
        # Current pipeline outputs.
        build_dir / "BOOK.MD_FINAL",
        build_dir / f"merge_premium_watson_{lang}.txt",
        build_dir / "merge_premium_watson.txt",
        build_dir / f"BOOK.PRE_EDITION.{lang}.md",
        build_dir / "BOOK.PRE_EDITION.md",
        build_dir / f"BOOK.PRE_QA.{lang}.md",
        build_dir / "BOOK.PRE_QA.md",
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
    template_path = Path(__file__).with_name("epub.css")
    if template_path.exists():
        css_path.write_text(template_path.read_text(encoding="utf-8"), encoding="utf-8")
        return css_path

    css_path.write_text(
        (
            "body { margin: 0 4%; }\n"
            "p { text-indent: 0 !important; margin: 0 0 0.9em 0; }\n"
            "img { display: block; margin: 1.2em auto !important; max-width: 100%; height: auto; }\n"
            "p.image-block, p.illustration, div.image-block, div.illustration { text-align: center; }\n"
            "p > img:only-child, p > img { display: block; margin: 1.2em auto !important; }\n"
            "figure, .figure, .chapter-illustration, .post-cover-illustration { margin: 1.2em auto; text-align: center; }\n"
            "figure img, .figure img, .chapter-illustration img, .post-cover-illustration img { margin: 0 auto !important; }\n"
            "h1, h2, h3, h4, h5, h6 { text-indent: 0 !important; }\n"
            "h2 { break-before: page; page-break-before: always; margin-top: 0; }\n"
        ),
        encoding="utf-8",
    )
    return css_path


def build_merged_kdp_source(edition: Edition) -> Path:
    fm_base = frontmatter_dir(edition)
    builds_base = builds_dir(edition)
    builds_base.mkdir(parents=True, exist_ok=True)

    sections: list[str] = []
    for filename in ["frontispiece.md", "copyright.md", "about_this_book.md", "about_contributor.md", "preface.md", "introduction.md"]:
        path = fm_base / filename
        if path.exists():
            txt = _normalize_pagebreaks(path.read_text(encoding="utf-8").rstrip())
            if filename == "frontispiece.md":
                txt = _remove_unwanted_taglines(txt).rstrip()
            if txt.strip():
                sections.append(txt + "\n\n")

    miolo_path = resolve_miolo_source_path(edition)
    _publish_legacy_miolo_snapshot(edition, miolo_path)

    miolo_txt = miolo_path.read_text(encoding="utf-8").strip()
    core_txt, supplements_txt = _split_core_and_supplements(miolo_txt)

    core_txt = _collapse_orphan_numbered_paragraphs(core_txt).strip()
    core_txt = _ensure_miolo_headings(core_txt, edition.language.code).strip()
    core_txt = _remove_unwanted_taglines(core_txt).strip()
    core_txt = _remove_manual_contents_block(core_txt).strip()
    core_txt = _normalize_chapter_headings(core_txt, edition.language.code).strip()
    if edition.work.code == "book_0029" and edition.language.code.lower() in {"en", "en-us"}:
        core_txt = _apply_book_0029_editorial_titles(core_txt).strip()
        _assert_book_0029_editorial_titles(core_txt)
        _assert_chapter_headings_short(core_txt)
    core_txt = _demote_pre_chapter_headings(core_txt).strip()
    core_txt = _normalize_pre_chapter_prelude(core_txt, edition).strip()
    if edition.work.code == "0031 epictetus — the enchiridion" and edition.language.code.lower() == "fr":
        core_txt = _apply_book_0031_fr_editorial_shape(core_txt).strip()
    core_txt = _renumber_core_aphorisms(core_txt).strip()
    if not (edition.work.code == "0031 epictetus — the enchiridion" and edition.language.code.lower() == "fr"):
        core_txt = _insert_visual_chapter_titles(core_txt).strip()
    core_txt = _remove_unwanted_taglines(core_txt).strip()
    core_txt = _normalize_pagebreaks(core_txt).strip()

    supplements_txt = _collapse_orphan_numbered_paragraphs(supplements_txt).strip()
    supplements_txt = _promote_supplement_headings(supplements_txt).strip()
    supplements_txt = _clean_supplement_false_headings(supplements_txt).strip()
    supplements_txt = _format_glossary_as_ordered_list(supplements_txt).strip()
    supplements_txt = _bold_glossary_headwords(supplements_txt)
    supplements_txt = _inline_glossary_continuations(supplements_txt).strip()
    supplements_txt = _normalize_glossary_inline_format(supplements_txt).strip()
    glossary_entries = _extract_glossary_entries(supplements_txt)
    supplements_txt = _inject_glossary_ids(supplements_txt, glossary_entries).strip()
    core_txt = _annotate_first_glossary_mentions(core_txt, glossary_entries).strip()
    external_glossary_entries = _load_external_glossary_entries(edition, builds_base)

    if supplements_txt:
        miolo_txt = f"{core_txt}\n\n{supplements_txt}"
    else:
        miolo_txt = core_txt
    if external_glossary_entries:
        miolo_txt = _link_external_glossary_markers(miolo_txt, external_glossary_entries)
    miolo_txt, cleanup_matches = _clean_leaked_body_markers(miolo_txt)
    miolo_txt, image_alt_matches = _clean_technical_image_alt_markers(miolo_txt)
    miolo_txt = _normalize_pagebreaks(miolo_txt).strip()
    cleanup_matches.extend(image_alt_matches)
    _write_marker_cleanup_report(builds_base, cleanup_matches)
    epilogue_path = fm_base / "epilogue.md"
    epilogue_txt = ""
    if epilogue_path.exists():
        epilogue_txt = _normalize_pagebreaks(epilogue_path.read_text(encoding="utf-8").rstrip()).strip()
        epilogue_txt = _annotate_first_glossary_mentions(epilogue_txt, glossary_entries).strip()
        if external_glossary_entries:
            epilogue_txt = _link_external_glossary_markers(epilogue_txt, external_glossary_entries)
    glossaire_path = fm_base / "glossaire.md"
    glossaire_txt = ""
    if glossaire_path.exists():
        glossaire_txt = _normalize_pagebreaks(glossaire_path.read_text(encoding="utf-8").rstrip()).strip()
        if edition.work.code == "0031 epictetus — the enchiridion" and edition.language.code.lower() == "fr":
            glossaire_txt = _add_book_0031_fr_glossaire_anchors(glossaire_txt)
        glossaire_txt = _annotate_first_glossary_mentions(glossaire_txt, glossary_entries).strip()
        if external_glossary_entries:
            glossaire_txt = _link_external_glossary_markers(glossaire_txt, external_glossary_entries)

    merged_txt = "".join(sections) + "\n\n" + miolo_txt.strip()
    if epilogue_txt:
        merged_txt += "\n\n" + epilogue_txt
    if glossaire_txt:
        merged_txt += "\n\n" + glossaire_txt
    external_glossary_txt = _build_external_glossary_markdown(external_glossary_entries)
    if external_glossary_txt:
        merged_txt += "\n\n" + external_glossary_txt.strip()
    merged_txt += "\n"
    if edition.work.code == "0031 epictetus — the enchiridion" and edition.language.code.lower() == "fr":
        merged_txt = _link_book_0031_fr_glossaire_terms(merged_txt)
    _repair_missing_referenced_assets(edition, builds_base, merged_txt)

    kdp_merged_path = builds_base / "kdp_merged.md"
    book_build_path = builds_base / "BOOK.BUILD.MD"

    kdp_merged_path.write_text(merged_txt, encoding="utf-8")
    book_build_path.write_text(merged_txt, encoding="utf-8")

    return kdp_merged_path


def _rewrite_glossary_internal_links(epub_path: Path) -> None:
    if not epub_path.exists():
        return

    glossary_member: str | None = None
    with zipfile.ZipFile(epub_path, "r") as zin:
        for name in zin.namelist():
            if not name.endswith(".xhtml"):
                continue
            data = zin.read(name).decode("utf-8", errors="ignore")
            if re.search(r'id="glossary-(?:term-\d+|[a-z]\d{2})"', data):
                glossary_member = name
                break

        if not glossary_member:
            return

        glossary_href = glossary_member.rsplit("/", 1)[-1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=".epub", dir=str(epub_path.parent)) as tmp:
            temp_path = Path(tmp.name)

        try:
            with zipfile.ZipFile(temp_path, "w") as zout:
                for info in zin.infolist():
                    raw = zin.read(info.filename)
                    if info.filename.endswith(".xhtml"):
                        text = raw.decode("utf-8", errors="ignore")
                        if info.filename != glossary_member:
                            text = re.sub(
                                r'href="#(glossary-(?:term-\d+|[a-z]\d{2}))"',
                                rf'href="{glossary_href}#\1"',
                                text,
                            )
                        raw = text.encode("utf-8")
                    zout.writestr(info, raw)
            shutil.move(str(temp_path), epub_path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)


def build_epub_for_edition(edition: Edition, epub_filename: str = "BOOK.epub") -> Path:
    # The renderer owns XHTML/CSS/navigation. The builder only packages the
    # exact artifact tree that was rendered and approved in preview.
    return EditionRenderer(edition).build_epub(epub_filename, require_approval=True)


def build_kdp_for_edition(edition: Edition) -> dict:
    build_frontmatter_files(edition, storage.frontmatter_dir())
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
        f"--resource-path={str(builds_base)}",
        "-V",
        "geometry:paperwidth=6in,paperheight=9in,margin=2cm",
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
    candidates = [
        builds_base / "BOOK.epub",
        builds_base / "BOOK.EPUB3",
        builds_base / "ebook.epub",
    ]
    epub_path = next((path for path in candidates if path.exists()), None)
    if epub_path is None:
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
    build_frontmatter_files(edition, storage.frontmatter_dir())
    merged_path = build_merged_kdp_source(edition)
    book_build_path = builds_dir(edition) / "BOOK.BUILD.MD"

    epub_path = build_epub_for_edition(edition)
    book_epub3 = builds_dir(edition) / "BOOK.EPUB3"
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
