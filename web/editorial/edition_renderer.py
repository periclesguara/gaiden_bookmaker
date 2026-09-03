from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import re
import shutil
import subprocess
import uuid
import zipfile
from dataclasses import dataclass
from datetime import timezone as datetime_timezone
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup, NavigableString, Tag

from gaiden.infrastructure import storage


THEME_NAME = "gaiden_epub_premium"
PUBLISHER_NAME = "Rinobooks"
THEME_ROOT = Path(__file__).with_name("themes") / THEME_NAME
PREVIEW_DIRNAME = "premium_epub_preview"
STATE_FILENAME = "premium_epub_state.json"
MANIFEST_FILENAME = "render_manifest.json"
CONTROLLED_THEME_KEYS = {
    "theme",
    "paragraph_indent",
    "body_line_height",
    "text_alignment",
    "first_paragraph_no_indent",
    "chapter_page_break",
    "chapter_title_centered",
    "chapter_title_bold",
    "chapter_ornament_enabled",
    "visible_contents",
    "include_ncx",
    "include_the_end",
    "illustration_opening_separate_xhtml",
    "show_illustration_captions",
    "about_page_type",
}

_PART_RE = re.compile(r"^(part|parte|partie|teil|libro|livre)\b", re.IGNORECASE)
_CHAPTER_RE = re.compile(
    r"^(chapter|cap[ií]tulo|chapitre|capitolo|kapitel|adventure)\s*([ivxlcdm]+|\d+)?\b[\s:—–-]*(.*)$",
    re.IGNORECASE,
)
_BACKMATTER_RE = re.compile(
    r"^(appendix|appendice|ap[eê]ndice|epilogue|[eé]pilogue|ep[ií]logo|glossary|glossaire|gloss[aá]rio|notes?|notas?)\b",
    re.IGNORECASE,
)
_END_RE = re.compile(r"^(the end|fim|fin|ende|fine)$", re.IGNORECASE)
_PIPELINE_MARKER_RE = re.compile(
    r"^\s*(?:RELEASE\s+STAMP|STAMP|::: ?pagebreak|CH\d{1,3}:\d{1,3})\s*$",
    re.IGNORECASE,
)
_GUTENBERG_RE = re.compile(
    r"project gutenberg(?: literary archive foundation)?|www\.gutenberg\.org",
    re.IGNORECASE,
)
_BARE_ROMAN_HEADING_RE = re.compile(
    r"^(?=[MDCLXVI]+$)M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$",
    re.IGNORECASE,
)
_ILLUSTRATION_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
_COVER_STEMS = {"0", "00", "capa", "cover", "frontcover"}


@dataclass
class RenderUnit:
    kind: str
    section_id: str
    title: str
    number: str
    filename: str
    body_html: str = ""
    opening_filename: str = ""


@dataclass(frozen=True)
class RenderResult:
    root: Path
    manifest_path: Path
    fingerprint: str
    spine: tuple[str, ...]
    warnings: tuple[str, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _media_type(path: Path) -> str:
    special = {
        ".xhtml": "application/xhtml+xml",
        ".ncx": "application/x-dtbncx+xml",
        ".css": "text/css",
        ".svg": "image/svg+xml",
    }
    return special.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _slug(value: str, fallback: str) -> str:
    normalized = value.casefold()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return normalized or fallback


def _pandoc_markdown_fragment(source: str, *, allow_raw_html: bool = False) -> str:
    input_format = (
        "markdown+smart"
        if allow_raw_html
        else "markdown+smart+hard_line_breaks-raw_html"
    )
    result = subprocess.run(
        ["pandoc", "-f", input_format, "-t", "html5", "--wrap=none"],
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Pandoc Markdown parse failed: {result.stderr.strip()}")
    return result.stdout


def _markdown_blocks(value: str, *, centered: bool = False) -> str:
    """Render trusted editorial Markdown without allowing raw HTML through."""
    fragment = _pandoc_markdown_fragment((value or "").strip())
    soup = BeautifulSoup(fragment, "html.parser")
    allowed = {
        "blockquote",
        "br",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "li",
        "ol",
        "p",
        "strong",
        "ul",
    }
    for tag in list(soup.find_all(True)):
        if tag.name not in allowed:
            tag.unwrap()
            continue
        tag.attrs = {}

    paragraphs = soup.find_all("p")
    for index, paragraph in enumerate(paragraphs):
        if centered:
            paragraph["class"] = ["no-indent", "centered"]
        elif index == 0:
            paragraph["class"] = ["first-paragraph"]
    return "".join(str(node) for node in soup.contents)


def _without_repeated_heading(value: str, heading: str) -> str:
    lines = (value or "").splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        normalized_line = re.sub(r"[^\w]+", " ", line, flags=re.UNICODE).strip().casefold()
        normalized_heading = re.sub(r"[^\w]+", " ", heading, flags=re.UNICODE).strip().casefold()
        if normalized_line == normalized_heading:
            del lines[index]
        break
    return "\n".join(lines).strip()


class EditionRenderer:
    """Canonical renderer for premium reflowable EPUB artifacts.

    Rendering creates the exact XHTML, CSS, navigation and resources consumed by
    both preview and final packaging. Packaging never regenerates content.
    """

    def __init__(self, edition, *, source_path: Path | None = None):
        self.edition = edition
        self.source_path = Path(source_path) if source_path else None
        self.build_dir = storage.builds_dir(edition.work.code, edition.language.code)
        self.preview_root = self.build_dir / PREVIEW_DIRNAME
        self.state_path = self.build_dir / STATE_FILENAME
        self.theme = json.loads((THEME_ROOT / "theme.json").read_text(encoding="utf-8"))
        override_path = self.build_dir / "edition_theme.json"
        if override_path.exists():
            overrides = json.loads(override_path.read_text(encoding="utf-8"))
            unknown = set(overrides) - CONTROLLED_THEME_KEYS
            if unknown:
                raise ValueError(f"Unsupported premium theme overrides: {sorted(unknown)}")
            if overrides.get("theme", THEME_NAME) != THEME_NAME:
                raise ValueError("A per-edition CSS fork is not allowed")
            self.theme.update(overrides)
        self.warnings: list[str] = []
        self._metadata: dict[str, object] | None = None

    def _epub_metadata(self) -> dict[str, object]:
        """Load optional edition-owned EPUB metadata without changing the data model.

        The build-side file is deliberately narrow: it supplements immutable
        bibliographic fields already stored on the edition and also holds
        accessible descriptions for saved Intake illustrations.
        """
        if self._metadata is not None:
            return self._metadata

        path = self.build_dir / "epub_metadata.json"
        if not path.exists():
            self._metadata = {}
            return self._metadata
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid EPUB metadata JSON: {path}") from exc
        if not isinstance(payload, dict) or payload.get("schema") != "gaiden_epub_metadata_v1":
            raise ValueError(f"Invalid EPUB metadata schema: {path}")

        allowed = {
            "schema", "language", "publisher", "rights", "description", "subjects",
            "date", "contributors", "illustration_alt_text",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"Unsupported EPUB metadata fields: {sorted(unknown)}")
        for key in ("language", "publisher", "rights", "description", "date"):
            if key in payload and (not isinstance(payload[key], str) or not payload[key].strip()):
                raise ValueError(f"EPUB metadata field {key!r} must be a non-empty string")
        if "subjects" in payload and (
            not isinstance(payload["subjects"], list)
            or not all(isinstance(value, str) and value.strip() for value in payload["subjects"])
        ):
            raise ValueError("EPUB metadata subjects must be a list of non-empty strings")
        if "contributors" in payload and (
            not isinstance(payload["contributors"], list)
            or not all(
                isinstance(value, dict)
                and isinstance(value.get("name"), str)
                and value["name"].strip()
                and isinstance(value.get("role"), str)
                and re.fullmatch(r"[a-z]{3}", value["role"])
                for value in payload["contributors"]
            )
        ):
            raise ValueError("EPUB metadata contributors require name and a three-letter MARC role")
        if "illustration_alt_text" in payload and (
            not isinstance(payload["illustration_alt_text"], dict)
            or not all(isinstance(key, str) and isinstance(value, str) and value.strip()
                       for key, value in payload["illustration_alt_text"].items())
        ):
            raise ValueError("Illustration alt text must map filenames to non-empty strings")
        self._metadata = payload
        return self._metadata

    def _template(self, name: str, **context: str) -> str:
        value = (THEME_ROOT / "templates" / name).read_text(encoding="utf-8")
        for key, replacement in context.items():
            value = value.replace("{{" + key + "}}", replacement)
        unresolved = re.findall(r"\{\{[^}]+\}\}", value)
        if unresolved:
            raise ValueError(f"Template {name} has unresolved values: {unresolved}")
        return value

    def _document(self, title: str, epub_type: str, content: str) -> str:
        language = html.escape(str(self._epub_metadata().get("language") or self.edition.language.code))
        return self._template(
            "document.xhtml",
            language=language,
            document_title=html.escape(title),
            epub_type=html.escape(epub_type),
            content=content,
        )

    def _source(self) -> tuple[str, Path]:
        candidates = [
            self.source_path,
            self.build_dir / "BOOK.MD_FINAL",
            self.build_dir / "kdp_merged.md",
            self.build_dir / f"BOOK.PRE_EDITION.{self.edition.language.code}.md",
            self.build_dir / "BOOK.PRE_EDITION.md",
            self.build_dir / f"BOOK.PRE_QA.{self.edition.language.code}.md",
            self.build_dir / "BOOK.PRE_QA.md",
        ]
        for candidate in candidates:
            if candidate and candidate.exists() and candidate.stat().st_size:
                return candidate.read_text(encoding="utf-8"), candidate.parent
        try:
            text = self.edition.texts.normalized_text or self.edition.texts.raw_text
        except Exception:
            text = ""
        if text.strip():
            return text, self.build_dir
        raise FileNotFoundError(
            f"No approved textual source found for {self.edition.work.code} [{self.edition.language.code}]"
        )

    def _clean_source(self, source: str) -> str:
        lines = [line for line in source.splitlines() if not _PIPELINE_MARKER_RE.match(line)]
        cleaned = "\n".join(lines).strip() + "\n"
        if _GUTENBERG_RE.search(cleaned):
            raise ValueError("Project Gutenberg material detected; premium render blocked")
        return cleaned

    def _apply_chapter_structure(self, source: str) -> str:
        """Apply an optional, edition-owned chapter and illustration map."""
        structure_path = self.build_dir / "chapter_structure.json"
        if not structure_path.exists():
            return source
        payload = json.loads(structure_path.read_text(encoding="utf-8"))
        chapters = payload.get("chapters")
        if payload.get("schema") != "gaiden_chapter_structure_v1" or not isinstance(chapters, list):
            raise ValueError(f"Invalid chapter structure: {structure_path}")

        lines = source.splitlines()
        for index, chapter in enumerate(chapters, start=1):
            anchor = str(chapter.get("anchor") or "").strip()
            title = str(chapter.get("title") or "").strip()
            image_path = str(chapter.get("image") or "").strip()
            if not anchor or not title:
                raise ValueError(f"Chapter {index} requires anchor and title")
            matches = [line_index for line_index, line in enumerate(lines) if line.strip().startswith(anchor)]
            if len(matches) != 1:
                raise ValueError(
                    f"Chapter {index} anchor must match exactly once ({len(matches)} found): {anchor}"
                )

            line_index = matches[0]
            heading = f"# Chapter {index:02d} — {title}"
            if lines[line_index].lstrip().startswith("#"):
                lines[line_index] = heading
                insert_at = line_index + 1
            else:
                lines[line_index:line_index] = [heading, ""]
                insert_at = line_index + 2

            if image_path:
                try:
                    self._resolve_image(image_path, self.build_dir)
                except FileNotFoundError:
                    self.warnings.append(
                        f"Ilustração do capítulo {index:02d} ausente: {image_path}"
                    )
                else:
                    lines[insert_at:insert_at] = [
                        f"![Illustration for Chapter {index:02d}]({image_path})",
                        "",
                    ]
        return "\n".join(lines).strip() + "\n"

    def _markdown_fragment(self, source: str) -> str:
        return _pandoc_markdown_fragment(source, allow_raw_html=True)

    @staticmethod
    def _chapter_label(match: re.Match[str], heading: str) -> tuple[str, str]:
        keyword = match.group(1).strip()
        numeral = (match.group(2) or "").strip()
        remainder = (match.group(3) or "").strip()
        number = " ".join(part for part in (keyword, numeral) if part)
        return number, remainder or heading

    def _split_units(self, fragment: str) -> list[RenderUnit]:
        soup = BeautifulSoup(fragment, "html.parser")
        roman_chapter_mode = len(
            [
                node
                for node in soup.find_all({"h1", "h2"})
                if _BARE_ROMAN_HEADING_RE.fullmatch(node.get_text(" ", strip=True))
            ]
        ) >= 2
        units: list[RenderUnit] = []
        current: RenderUnit | None = None
        nodes: list[str] = []
        chapter_index = 0
        part_index = 0
        backmatter_index = 0

        def flush() -> None:
            nonlocal current, nodes
            if current is None:
                nodes = []
                return
            current.body_html = self._decorate_body("".join(nodes))
            units.append(current)
            current = None
            nodes = []

        for node in soup.contents:
            if isinstance(node, NavigableString) and not str(node).strip():
                continue
            if isinstance(node, Tag) and node.name in {"h1", "h2"}:
                heading = node.get_text(" ", strip=True)
                if _END_RE.fullmatch(heading):
                    flush()
                    continue
                if roman_chapter_mode:
                    if _BARE_ROMAN_HEADING_RE.fullmatch(heading):
                        flush()
                        chapter_index += 1
                        label = f"Chapter {heading.upper()}"
                        current = RenderUnit(
                            "chapter",
                            f"chapter-{chapter_index:03d}",
                            label,
                            label,
                            f"chapter_{chapter_index:03d}.xhtml",
                        )
                        continue
                    if current is None:
                        continue
                if node.name == "h1" and _PART_RE.match(heading):
                    flush()
                    part_index += 1
                    units.append(
                        RenderUnit("part", f"part-{part_index:02d}", heading, heading, f"part_{part_index:02d}.xhtml")
                    )
                    continue
                chapter_match = _CHAPTER_RE.match(heading)
                if chapter_match:
                    flush()
                    chapter_index += 1
                    number, title = self._chapter_label(chapter_match, heading)
                    current = RenderUnit(
                        "chapter",
                        f"chapter-{chapter_index:03d}",
                        title,
                        number,
                        f"chapter_{chapter_index:03d}.xhtml",
                    )
                    continue
                if node.name == "h1" and _BACKMATTER_RE.match(heading):
                    flush()
                    backmatter_index += 1
                    current = RenderUnit(
                        "backmatter",
                        f"backmatter-{backmatter_index:02d}-{_slug(heading, 'section')}",
                        heading,
                        "",
                        f"backmatter_{backmatter_index:02d}.xhtml",
                    )
                    continue
            # Content before/between semantic sections is not an introduction.
            # Frontmatter is rendered from its explicit editorial fields, so a
            # title-page remnant or intake marker must never create an empty or
            # synthetic "Introduction" page in the EPUB.
            if current is None:
                continue
            nodes.append(str(node))
        flush()
        if not any(unit.kind == "chapter" for unit in units):
            raise ValueError("Premium render requires at least one semantic chapter")
        return units

    @staticmethod
    def _decorate_body(fragment: str) -> str:
        soup = BeautifulSoup(fragment, "html.parser")
        for image in soup.find_all("img"):
            if not image.get("alt"):
                image["alt"] = "Illustration"
            classes = list(image.get("class") or [])
            if "illustration" not in classes:
                classes.append("illustration")
            image["class"] = classes
            if image.parent and image.parent.name == "p" and not image.parent.get_text(strip=True):
                figure = soup.new_tag("figure")
                image.parent.replace_with(figure)
                figure.append(image)
        first_paragraph = soup.find("p")
        if first_paragraph:
            classes = list(first_paragraph.get("class") or [])
            if "first-paragraph" not in classes:
                classes.append("first-paragraph")
            first_paragraph["class"] = classes
        return "".join(str(node) for node in soup.contents)

    def _resolve_image(self, src: str, source_base: Path) -> Path:
        raw = Path(src)
        candidates = [
            raw if raw.is_absolute() else None,
            source_base / raw,
            self.build_dir / raw,
            storage.repo_root() / raw,
        ]
        for candidate in candidates:
            if candidate and candidate.exists() and candidate.is_file():
                return candidate.resolve()
        raise FileNotFoundError(f"Referenced illustration not found: {src}")

    def _copy_unit_images(self, units: list[RenderUnit], images_dir: Path, source_base: Path) -> list[Path]:
        copied: dict[Path, str] = {}
        used_names: dict[str, Path] = {}
        references: set[Path] = set()
        outputs: list[Path] = []
        for unit in units:
            soup = BeautifulSoup(unit.body_html, "html.parser")
            for image in soup.find_all("img"):
                src = str(image.get("src") or "").strip()
                if not src or src.startswith(("http://", "https://", "data:")):
                    raise ValueError(f"EPUB illustrations must be local resources: {src}")
                resolved = self._resolve_image(src, source_base)
                if resolved in references:
                    raise ValueError(f"Duplicate illustration reference: {resolved.name}")
                references.add(resolved)
                filename = copied.get(resolved)
                if filename is None:
                    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", resolved.name)
                    if filename in used_names and used_names[filename] != resolved:
                        filename = f"{resolved.stem}_{_sha256(resolved)[:8]}{resolved.suffix.lower()}"
                    shutil.copy2(resolved, images_dir / filename)
                    copied[resolved] = filename
                    used_names[filename] = resolved
                    outputs.append(images_dir / filename)
                image["src"] = f"../images/{filename}"
            unit.body_html = "".join(str(node) for node in soup.contents)
        return outputs

    @staticmethod
    def _illustration_sort_key(path: Path) -> tuple[tuple[int, object], ...]:
        return tuple(
            (0, int(part)) if part.isdigit() else (1, part)
            for part in re.split(r"(\d+)", path.name.casefold())
        )

    def _saved_illustrations(self) -> list[Path]:
        language = self.edition.language.code.strip().lower()
        language_candidates = [language, language.replace("-", "_")]
        if "_" in language_candidates[-1]:
            language_candidates.append(language_candidates[-1].split("_", 1)[0])

        found: dict[Path, Path] = {}
        for language_code in dict.fromkeys(language_candidates):
            images_dir = storage.images_dir(self.edition.work.code, language_code)
            if not images_dir.exists():
                continue
            for path in images_dir.rglob("*"):
                if not path.is_file() or path.suffix.casefold() not in _ILLUSTRATION_EXTENSIONS:
                    continue
                normalized_stem = re.sub(r"[^a-z0-9]+", "", path.stem.casefold())
                numbers = [int(value) for value in re.findall(r"\d+", path.stem)]
                if normalized_stem in _COVER_STEMS or (numbers and numbers[0] == 0):
                    continue
                found[path.resolve()] = path.resolve()
        return sorted(found.values(), key=self._illustration_sort_key)

    def _attach_saved_illustrations(self, units: list[RenderUnit]) -> None:
        """Attach Intake gallery images to chapters by their numeric filename."""
        chapters = [unit for unit in units if unit.kind == "chapter"]
        images = self._saved_illustrations()
        if not chapters or not images:
            return

        chapters_with_images = {
            index
            for index, unit in enumerate(chapters, start=1)
            if BeautifulSoup(unit.body_html, "html.parser").find("img") is not None
        }
        assignments: dict[int, list[Path]] = {}
        unnumbered: list[Path] = []
        skipped = 0

        for path in images:
            numbers = [int(value) for value in re.findall(r"\d+", path.stem)]
            chapter_number = numbers[0] if numbers else None
            if chapter_number is None:
                unnumbered.append(path)
            elif chapter_number < 1 or chapter_number > len(chapters):
                skipped += 1
            elif chapter_number in chapters_with_images:
                continue
            else:
                assignments.setdefault(chapter_number, []).append(path)

        available_chapters = [
            index
            for index in range(1, len(chapters) + 1)
            if index not in chapters_with_images and index not in assignments
        ]
        for path, chapter_number in zip(unnumbered, available_chapters):
            assignments.setdefault(chapter_number, []).append(path)
        skipped += max(0, len(unnumbered) - len(available_chapters))

        for chapter_number, paths in assignments.items():
            unit = chapters[chapter_number - 1]
            soup = BeautifulSoup(unit.body_html, "html.parser")
            for path in reversed(paths):
                figure = soup.new_tag("figure")
                image = soup.new_tag("img")
                image["src"] = str(path)
                alt_text = self._epub_metadata().get("illustration_alt_text", {})
                image["alt"] = (
                    alt_text.get(path.name, f"Illustration for Chapter {chapter_number:02d}")
                    if isinstance(alt_text, dict)
                    else f"Illustration for Chapter {chapter_number:02d}"
                )
                image["class"] = ["illustration"]
                figure.append(image)
                soup.insert(0, figure)
            unit.body_html = "".join(str(node) for node in soup.contents)

        if skipped:
            self.warnings.append(
                f"{skipped} saved illustration(s) could not be linked to an unillustrated chapter"
            )

    def _frontmatter_template(self):
        try:
            from pipeline.models import BookEditionTemplate

            language = self.edition.language.code.lower().replace("-", "")
            if language in {"ptbr", "pt_br"}:
                language = "ptbr"
            else:
                language = language.split("_")[0]
            return BookEditionTemplate.objects.filter(
                book_code=self.edition.work.code,
                language=language,
            ).first()
        except Exception:
            return None

    def _labels(self) -> dict[str, str]:
        code = self.edition.language.code.lower().replace("_", "-")
        base = code.split("-")[0]
        labels = {
            "en": {"cover": "Cover", "title_page": "Title Page", "frontispiece": "Frontispiece", "copyright": "Copyright", "about": "About This Book", "contents": "Contents", "end": "The End"},
            "pt": {"cover": "Capa", "title_page": "Folha de rosto", "frontispiece": "Frontispício", "copyright": "Direitos Autorais", "about": "Sobre Este Livro", "contents": "Sumário", "end": "Fim"},
            "fr": {"cover": "Couverture", "title_page": "Page de titre", "frontispiece": "Frontispice", "copyright": "Droits d’auteur", "about": "À propos de ce livre", "contents": "Sommaire", "end": "Fin"},
            "es": {"cover": "Portada", "title_page": "Página de título", "frontispiece": "Frontispicio", "copyright": "Derechos de autor", "about": "Sobre este libro", "contents": "Contenido", "end": "Fin"},
            "de": {"cover": "Umschlag", "title_page": "Titelseite", "frontispiece": "Frontispiz", "copyright": "Urheberrecht", "about": "Über dieses Buch", "contents": "Inhalt", "end": "Ende"},
            "it": {"cover": "Copertina", "title_page": "Frontespizio", "frontispiece": "Antiporta", "copyright": "Copyright", "about": "Su questo libro", "contents": "Indice", "end": "Fine"},
        }
        return labels.get(base, labels["en"])

    def _write_xhtml(self, text_dir: Path, filename: str, title: str, epub_type: str, content: str) -> None:
        (text_dir / filename).write_text(self._document(title, epub_type, content), encoding="utf-8")

    def _theme_css(self) -> str:
        css = (THEME_ROOT / "styles" / "gaiden-premium.css").read_text(encoding="utf-8")
        indent = str(self.theme.get("paragraph_indent", "1.25em"))
        line_height = str(self.theme.get("body_line_height", 1.5))
        alignment = str(self.theme.get("text_alignment", "justify"))
        if not re.fullmatch(r"\d+(?:\.\d+)?(?:em|rem|%)", indent):
            raise ValueError("paragraph_indent must use a relative unit")
        if alignment not in {"justify", "left"}:
            raise ValueError("text_alignment must be justify or left")
        css = css.replace("1.25em", indent)
        body_match = re.search(r"body\s*\{.*?\}", css, re.DOTALL)
        if body_match:
            body = re.sub(r"line-height:\s*[^;]+", f"line-height: {line_height}", body_match.group(0), count=1)
            body = re.sub(r"text-align:\s*[^;]+", f"text-align: {alignment}", body, count=1)
            css = css[: body_match.start()] + body + css[body_match.end() :]
        return css

    def _render_units(self, text_dir: Path, units: list[RenderUnit]) -> list[tuple[str, str, str]]:
        navigation: list[tuple[str, str, str]] = []
        for unit in units:
            if unit.kind == "part":
                content = self._template(
                    "part.xhtml",
                    section_id=html.escape(unit.section_id),
                    part_number=html.escape(unit.number),
                    part_title=html.escape(unit.title),
                )
                epub_type = "bodymatter"
            elif unit.kind == "chapter":
                same_label = unit.title.casefold() == unit.number.casefold()
                chapter_number_html = "" if same_label else f'<p class="chapter-number">{html.escape(unit.number)}</p>'
                ornament = '<div class="chapter-ornament" aria-hidden="true">◆</div>' if self.theme.get("chapter_ornament_enabled") else ""
                body_soup = BeautifulSoup(unit.body_html, "html.parser")
                first_element = next((node for node in body_soup.contents if isinstance(node, Tag)), None)
                opening_figure = first_element if first_element and first_element.name == "figure" else None
                if opening_figure and self.theme.get("illustration_opening_separate_xhtml", True):
                    image = opening_figure.find("img")
                    caption = opening_figure.find("figcaption")
                    unit.opening_filename = unit.filename.replace(".xhtml", "_opening.xhtml")
                    opening = self._template(
                        "chapter_opening.xhtml",
                        section_id=html.escape(unit.section_id),
                        chapter_number_html=chapter_number_html,
                        chapter_title=html.escape(unit.title),
                        image_filename=html.escape(str(image.get("src")).removeprefix("../images/")),
                        image_alt=html.escape(str(image.get("alt") or f"Illustration for {unit.title}")),
                        caption_html=str(caption) if caption and self.theme.get("show_illustration_captions") else "",
                    )
                    self._write_xhtml(text_dir, unit.opening_filename, unit.title, "bodymatter", opening)
                    opening_figure.extract()
                    unit.body_html = "".join(str(node) for node in body_soup.contents)
                    content = self._template(
                        "chapter_continuation.xhtml",
                        section_id=html.escape(f"{unit.section_id}-body"),
                        body_html=unit.body_html,
                    )
                else:
                    content = self._template(
                        "chapter_body.xhtml",
                        section_id=html.escape(unit.section_id),
                        chapter_number_html=chapter_number_html,
                        chapter_title=html.escape(unit.title),
                        ornament_html=ornament,
                        body_html=unit.body_html,
                    )
                epub_type = "bodymatter"
            elif unit.kind == "backmatter":
                section_type = "glossary" if "gloss" in unit.title.casefold() else "appendix"
                content = self._template(
                    "backmatter.xhtml",
                    section_type=section_type,
                    section_id=html.escape(unit.section_id),
                    heading=html.escape(unit.title),
                    body_html=unit.body_html,
                )
                epub_type = "backmatter"
            else:
                content = self._template(
                    "introduction.xhtml",
                    heading=html.escape(unit.title),
                    body_html=unit.body_html,
                )
                epub_type = "frontmatter"
            self._write_xhtml(text_dir, unit.filename, unit.title, epub_type, content)
            navigation.append(
                (
                    unit.opening_filename or unit.filename,
                    unit.section_id,
                    unit.number if unit.kind == "chapter" else unit.title,
                )
            )
        return navigation

    def _write_navigation(
        self,
        epub_dir: Path,
        text_dir: Path,
        navigation: list[tuple[str, str, str]],
        labels: dict[str, str],
    ) -> None:
        items = "".join(
            f'<li class="contents-{"part" if filename.startswith("part_") else "chapter"}"><a href="{html.escape(filename)}#{html.escape(section_id)}">{html.escape(label)}</a></li>'
            for filename, section_id, label in navigation
        )
        contents = self._template("contents.xhtml", heading=html.escape(labels["contents"]), items=items)
        self._write_xhtml(text_dir, "contents.xhtml", labels["contents"], "frontmatter", contents)

        nav_items = "".join(
            f'<li><a href="text/{html.escape(filename)}#{html.escape(section_id)}">{html.escape(label)}</a></li>'
            for filename, section_id, label in navigation
        )
        nav = self._document(
            labels["contents"],
            "frontmatter",
            f'<nav epub:type="toc" id="toc"><h1>{html.escape(labels["contents"])}</h1><ol>{nav_items}</ol></nav>',
        ).replace('href="../styles/', 'href="styles/')
        (epub_dir / "nav.xhtml").write_text(nav, encoding="utf-8")

        identifier = self._identifier()
        nav_points = []
        for order, (filename, section_id, label) in enumerate(navigation, start=1):
            nav_points.append(
                f'<navPoint id="nav-{order}" playOrder="{order}"><navLabel><text>{html.escape(label)}</text></navLabel><content src="text/{html.escape(filename)}#{html.escape(section_id)}"/></navPoint>'
            )
        ncx = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
            f'<head><meta name="dtb:uid" content="{html.escape(identifier)}"/></head>'
            f'<docTitle><text>{html.escape(self.edition.title)}</text></docTitle><navMap>{"".join(nav_points)}</navMap></ncx>'
        )
        (epub_dir / "toc.ncx").write_text(ncx, encoding="utf-8")

    def _identifier(self) -> str:
        value = f"gaiden:{self.edition.work.code}:{self.edition.language.code}"
        return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, value)}"

    def _modified(self) -> str:
        value = getattr(self.edition, "updated_at", None)
        if value is None:
            return "2000-01-01T00:00:00Z"
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime_timezone.utc)
        return value.astimezone(datetime_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _write_opf(self, epub_dir: Path, spine: list[str], cover_filename: str) -> None:
        files = sorted(path for path in epub_dir.rglob("*") if path.is_file() and path.name != "content.opf")
        manifest_rows = []
        id_by_href: dict[str, str] = {}
        for index, path in enumerate(files, start=1):
            href = path.relative_to(epub_dir).as_posix()
            item_id = "cover-image" if href == f"images/{cover_filename}" else f"item-{index:03d}"
            properties = ""
            if href == "nav.xhtml":
                properties = ' properties="nav"'
            elif item_id == "cover-image":
                properties = ' properties="cover-image"'
            id_by_href[href] = item_id
            manifest_rows.append(
                f'<item id="{item_id}" href="{html.escape(href)}" media-type="{_media_type(path)}"{properties}/>'
            )
        spine_rows = "".join(f'<itemref idref="{id_by_href[href]}"/>' for href in spine)
        template = self._frontmatter_template()
        metadata = self._epub_metadata()
        rights_holder = (getattr(template, "edition_copyright_holder", "") or PUBLISHER_NAME).strip()
        rights = str(metadata.get("rights") or (
            f"Original work in the public domain. This edition © {self.edition.edition_year or self.edition.publication_year} {rights_holder}."
        ))
        creator = (self.edition.author or self.edition.work.author.name).strip()
        language = str(metadata.get("language") or self.edition.language.code)
        publisher = str(metadata.get("publisher") or self.edition.publisher or PUBLISHER_NAME)
        date = str(metadata.get("date") or self.edition.edition_year or self.edition.publication_year)
        description = str(metadata.get("description") or "")
        subjects = metadata.get("subjects") or []
        contributors = metadata.get("contributors") or []
        contributor_rows = "".join(
            f'<dc:contributor id="contributor-{index}">{html.escape(str(value["name"]))}</dc:contributor>'
            f'<meta refines="#contributor-{index}" property="role" scheme="marc:relators">{html.escape(str(value["role"]))}</meta>'
            for index, value in enumerate(contributors, start=1)
        )
        subject_rows = "".join(f'<dc:subject>{html.escape(str(value))}</dc:subject>' for value in subjects)
        description_row = f'<dc:description>{html.escape(description)}</dc:description>' if description else ""
        opf = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:identifier id="pub-id">{html.escape(self._identifier())}</dc:identifier>'
            f'<dc:title>{html.escape(self.edition.title)}</dc:title>'
            f'<dc:creator>{html.escape(creator)}</dc:creator>'
            f'<dc:language>{html.escape(language)}</dc:language>'
            f'<dc:publisher>{html.escape(publisher)}</dc:publisher>'
            f'<dc:rights>{html.escape(rights)}</dc:rights>'
            f'<dc:date>{html.escape(date)}</dc:date>'
            f'{contributor_rows}{description_row}{subject_rows}'
            f'<meta property="dcterms:modified">{self._modified()}</meta>'
            '</metadata>'
            f'<manifest>{"".join(manifest_rows)}</manifest>'
            f'<spine toc="{id_by_href["toc.ncx"]}">{spine_rows}</spine>'
            '</package>'
        )
        (epub_dir / "content.opf").write_text(opf, encoding="utf-8")

    def _render_tree(self, root: Path) -> list[str]:
        epub_dir = root / "EPUB"
        text_dir = epub_dir / "text"
        styles_dir = epub_dir / "styles"
        images_dir = epub_dir / "images"
        meta_inf = root / "META-INF"
        for directory in (text_dir, styles_dir, images_dir, meta_inf):
            directory.mkdir(parents=True, exist_ok=True)
        (root / "mimetype").write_text("application/epub+zip", encoding="ascii")
        (meta_inf / "container.xml").write_text(
            '<?xml version="1.0" encoding="utf-8"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
            encoding="utf-8",
        )
        (styles_dir / "gaiden-premium.css").write_text(self._theme_css(), encoding="utf-8")

        source, source_base = self._source()
        source = self._clean_source(source)
        source = self._apply_chapter_structure(source)
        units = self._split_units(self._markdown_fragment(source))
        self._attach_saved_illustrations(units)
        self._copy_unit_images(units, images_dir, source_base)

        template = self._frontmatter_template()
        labels = self._labels()
        title = (self.edition.title or self.edition.work.title).strip()
        subtitle = (self.edition.subtitle or "").strip()
        author = (self.edition.author or self.edition.work.author.name).strip()
        title_page_seal = (
            getattr(template, "seal_name", "")
            or self.edition.seal_name
            or getattr(self.edition.seal, "name", "")
            or self.edition.imprint_name
            or PUBLISHER_NAME
        ).strip()

        cover_source = self._cover_path()
        if cover_source is None:
            raise FileNotFoundError("Premium EPUB requires an official cover image")
        cover_filename = f"cover{cover_source.suffix.lower()}"
        shutil.copy2(cover_source, images_dir / cover_filename)
        cover = self._template(
            "cover.xhtml",
            cover_filename=html.escape(cover_filename),
            cover_alt=html.escape(f"Cover of {title}"),
        )
        self._write_xhtml(text_dir, "cover.xhtml", title, "frontmatter", cover)
        title_page = self._template(
            "title_page.xhtml",
            title=html.escape(title),
            subtitle_html=f'<p class="book-subtitle">{html.escape(subtitle)}</p>' if subtitle else "",
            author=html.escape(author),
            seal=html.escape(title_page_seal),
        )
        self._write_xhtml(text_dir, "title_page.xhtml", title, "frontmatter", title_page)

        copyright_text = (getattr(template, "copyright_text", "") or self.edition.copyright_template or "").strip()
        copyright_text = _without_repeated_heading(copyright_text, labels["copyright"])
        copyright_page = self._template(
            "copyright.xhtml",
            heading=html.escape(labels["copyright"]),
            body_html=_markdown_blocks(copyright_text, centered=True),
        )
        self._write_xhtml(text_dir, "copyright.xhtml", labels["copyright"], "frontmatter", copyright_page)

        about_text = (getattr(template, "about_edition_text", "") or self.edition.about_edition_text or self.edition.about_edition_template or "").strip()
        about_text = _without_repeated_heading(about_text, labels["about"])
        about_page = self._template(
            "about_this_edition.xhtml",
            heading=html.escape(labels["about"]),
            body_html=_markdown_blocks(about_text),
        )
        self._write_xhtml(text_dir, "about_this_edition.xhtml", labels["about"], "frontmatter", about_page)

        body_navigation = self._render_units(text_dir, units)
        end_page = self._template("the_end.xhtml", heading=html.escape(labels["end"]))
        self._write_xhtml(text_dir, "the_end.xhtml", labels["end"], "backmatter", end_page)

        frontispiece_text = (getattr(template, "frontispiece_text", "") or "").strip()
        has_frontispiece = bool(frontispiece_text)
        if has_frontispiece:
            frontispiece = self._template(
                "frontispiece.xhtml",
                body_html=_markdown_blocks(frontispiece_text, centered=True),
            )
            self._write_xhtml(text_dir, "frontispiece.xhtml", "Frontispiece", "frontmatter", frontispiece)

        navigation = [
            ("cover.xhtml", "cover", labels["cover"]),
            ("title_page.xhtml", "title-page", labels["title_page"]),
        ]
        if has_frontispiece:
            navigation.append(("frontispiece.xhtml", "frontispiece", labels["frontispiece"]))
        navigation.extend(
            [
                ("copyright.xhtml", "copyright", labels["copyright"]),
                ("about_this_edition.xhtml", "about-this-edition", labels["about"]),
            ]
        )
        navigation.extend(body_navigation)
        navigation.append(("the_end.xhtml", "the-end", labels["end"]))
        self._write_navigation(epub_dir, text_dir, navigation, labels)

        spine = [
            "text/cover.xhtml",
            "text/title_page.xhtml",
        ]
        if has_frontispiece:
            spine.append("text/frontispiece.xhtml")
        spine.extend(
            [
                "text/copyright.xhtml",
                "text/contents.xhtml",
                "text/about_this_edition.xhtml",
            ]
        )
        for unit in units:
            if unit.opening_filename:
                spine.append(f"text/{unit.opening_filename}")
            spine.append(f"text/{unit.filename}")
        spine.append("text/the_end.xhtml")
        self._write_opf(epub_dir, spine, cover_filename)
        return spine

    def _cover_path(self) -> Path | None:
        cover_value = (getattr(self.edition, "cover_filepath", "") or "").strip()
        candidates = []
        if cover_value:
            candidates.append(storage.resolve_storage_path(cover_value))
        cover_dir = storage.covers_dir(self.edition.work.code, self.edition.language.code)
        candidates.extend(cover_dir / name for name in ("cover.jpg", "cover.jpeg", "cover.png"))
        return next((path for path in candidates if path.exists() and path.is_file()), None)

    def _manifest(self, root: Path, spine: list[str]) -> dict[str, object]:
        files = {
            path.relative_to(root).as_posix(): _sha256(path)
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.name != MANIFEST_FILENAME
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                {"files": files, "theme_config": self.theme},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "schema": "gaiden_premium_render_manifest_v1",
            "theme": THEME_NAME,
            "theme_config": self.theme,
            "edition": f"{self.edition.work.code}:{self.edition.language.code}",
            "fingerprint": fingerprint,
            "files": files,
            "spine": spine,
            "warnings": self.warnings,
        }

    def render(self) -> RenderResult:
        self.build_dir.mkdir(parents=True, exist_ok=True)
        temp_root = self.build_dir / f".{PREVIEW_DIRNAME}-{uuid.uuid4().hex}"
        temp_root.mkdir(parents=True)
        try:
            spine = self._render_tree(temp_root)
            manifest = self._manifest(temp_root, spine)
            (temp_root / MANIFEST_FILENAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.audit(temp_root, manifest)
            previous = self.build_dir / f".{PREVIEW_DIRNAME}-previous"
            if previous.exists():
                shutil.rmtree(previous)
            if self.preview_root.exists():
                self.preview_root.rename(previous)
            temp_root.rename(self.preview_root)
            if previous.exists():
                shutil.rmtree(previous)
        finally:
            if temp_root.exists():
                shutil.rmtree(temp_root)

        prior_state = self._load_state()
        fingerprint = str(manifest["fingerprint"])
        approved = prior_state.get("approved_fingerprint") == fingerprint
        state = {
            "schema": "gaiden_premium_render_state_v1",
            "theme": THEME_NAME,
            "render_fingerprint": fingerprint,
            "approved_fingerprint": fingerprint if approved else "",
            "status": "PREVIEW_APPROVED" if approved else "PREVIEW_APPROVAL_REQUIRED",
        }
        self._write_state(state)
        return RenderResult(
            root=self.preview_root,
            manifest_path=self.preview_root / MANIFEST_FILENAME,
            fingerprint=fingerprint,
            spine=tuple(spine),
            warnings=tuple(self.warnings),
        )

    def _load_state(self) -> dict[str, object]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _write_state(self, state: dict[str, object]) -> None:
        self.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def approve_preview(self) -> RenderResult:
        result = self.render()
        state = self._load_state()
        state["approved_fingerprint"] = result.fingerprint
        state["status"] = "PREVIEW_APPROVED"
        self._write_state(state)
        return result

    def verify_render(self) -> dict[str, object]:
        manifest_path = self.preview_root / MANIFEST_FILENAME
        if not manifest_path.exists():
            raise FileNotFoundError("Premium preview has not been rendered")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for relative, expected in manifest.get("files", {}).items():
            path = self.preview_root / relative
            if not path.exists() or _sha256(path) != expected:
                raise ValueError(f"Rendered artifact changed after preview: {relative}")
        return manifest

    def preview_is_approved(self) -> bool:
        try:
            manifest = self.verify_render()
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            return False
        state = self._load_state()
        return bool(
            state.get("status") == "PREVIEW_APPROVED"
            and state.get("approved_fingerprint") == manifest.get("fingerprint")
        )

    def epub_matches_preview(self, epub_path: Path) -> bool:
        if not self.preview_is_approved() or not epub_path.is_file():
            return False
        manifest = self.verify_render()
        expected = {
            relative: digest
            for relative, digest in manifest.get("files", {}).items()
            if relative != "mimetype"
        }
        try:
            actual = package_hashes(epub_path)
        except (OSError, zipfile.BadZipFile):
            return False
        return actual == expected

    def build_epub(self, filename: str = "BOOK.epub", *, require_approval: bool = True) -> Path:
        manifest = self.verify_render()
        state = self._load_state()
        if require_approval and (
            state.get("status") != "PREVIEW_APPROVED"
            or state.get("approved_fingerprint") != manifest.get("fingerprint")
        ):
            raise ValueError("Premium preview must be approved before EPUB packaging")
        output = self.build_dir / filename
        with zipfile.ZipFile(output, "w") as archive:
            mimetype_path = self.preview_root / "mimetype"
            archive.write(mimetype_path, "mimetype", compress_type=zipfile.ZIP_STORED)
            for path in sorted(self.preview_root.rglob("*")):
                if not path.is_file() or path == mimetype_path or path.name == MANIFEST_FILENAME:
                    continue
                archive.write(path, path.relative_to(self.preview_root).as_posix(), compress_type=zipfile.ZIP_DEFLATED)
        return output

    @staticmethod
    def audit(root: Path, manifest: dict[str, object] | None = None) -> None:
        epub_dir = root / "EPUB"
        css = (epub_dir / "styles" / "gaiden-premium.css").read_text(encoding="utf-8")
        forbidden_css = [r"width\s*:\s*600px", r"height\s*:\s*900px", r"position\s*:\s*(?:absolute|fixed)", r"display\s*:\s*(?:flex|grid)", r"overflow\s*:\s*hidden"]
        for pattern in forbidden_css:
            if re.search(pattern, css, re.IGNORECASE):
                raise ValueError(f"Forbidden fixed-layout CSS: {pattern}")
        body_rule = re.search(r"body\s*\{([^}]*)\}", css, re.IGNORECASE | re.DOTALL)
        if body_rule and re.search(r"text-align\s*:\s*center", body_rule.group(1), re.IGNORECASE):
            raise ValueError("Narrative body cannot be globally centered")
        if not re.search(r"img\s*\{[^}]*max-width\s*:\s*100%[^}]*height\s*:\s*auto", css, re.IGNORECASE | re.DOTALL):
            raise ValueError("Responsive image rule is missing")

        global_ids: set[str] = set()
        headings: set[str] = set()
        chapter_h1_counts: dict[str, int] = {}
        for path in sorted((epub_dir / "text").glob("*.xhtml")):
            soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
            for tag in soup.find_all(id=True):
                identifier = tag.get("id")
                if identifier in global_ids:
                    raise ValueError(f"Duplicate XHTML id: {identifier}")
                global_ids.add(identifier)
            if path.name.startswith("chapter_"):
                h1s = soup.find_all("h1")
                chapter_key = re.match(r"chapter_\d+", path.name).group(0)
                chapter_h1_counts[chapter_key] = chapter_h1_counts.get(chapter_key, 0) + len(h1s)
                for h1 in h1s:
                    heading = h1.get_text(" ", strip=True).casefold()
                    scoped = f"{chapter_key}:{heading}"
                    if scoped in headings:
                        raise ValueError(f"Duplicate chapter heading: {heading}")
                    headings.add(scoped)
                first_p = soup.select_one(".chapter-body p")
                if first_p and "first-paragraph" not in (first_p.get("class") or []):
                    raise ValueError(f"First paragraph is not marked in {path.name}")
        for chapter_key, count in chapter_h1_counts.items():
            if count != 1:
                raise ValueError(f"Logical chapter must have exactly one h1: {chapter_key} ({count})")
        opf = BeautifulSoup((epub_dir / "content.opf").read_text(encoding="utf-8"), "xml")
        cover = opf.find("item", attrs={"properties": re.compile(r"\bcover-image\b")})
        if cover is None:
            raise ValueError("OPF cover-image property is missing")
        item_by_id = {item.get("id"): item.get("href") for item in opf.find_all("item")}
        spine = [item_by_id.get(item.get("idref")) for item in opf.find_all("itemref")]
        if not spine or spine[0] != "text/cover.xhtml":
            raise ValueError("Cover must be the first spine item")
        expected_frontmatter = ["text/cover.xhtml", "text/title_page.xhtml"]
        if "text/frontispiece.xhtml" in spine:
            expected_frontmatter.append("text/frontispiece.xhtml")
        expected_frontmatter.extend(
            [
                "text/copyright.xhtml",
                "text/contents.xhtml",
                "text/about_this_edition.xhtml",
            ]
        )
        if spine[: len(expected_frontmatter)] != expected_frontmatter:
            raise ValueError("Frontmatter does not follow the canonical reading order")
        if "text/the_end.xhtml" not in spine:
            raise ValueError("The End page is missing from spine")
        if opf.find("dc:language") is None and opf.find("language") is None:
            raise ValueError("Language metadata is missing")
        package_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in epub_dir.rglob("*.xhtml"))
        if re.search(r"::: ?pagebreak|RELEASE\s+STAMP", package_text, re.IGNORECASE):
            raise ValueError("Internal pipeline marker leaked into XHTML")
        body_text = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in (epub_dir / "text").glob("chapter_*.xhtml")
        )
        if _GUTENBERG_RE.search(body_text):
            raise ValueError("Project Gutenberg material leaked into XHTML")


def package_hashes(epub_path: Path) -> dict[str, str]:
    with zipfile.ZipFile(epub_path) as archive:
        return {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in archive.namelist()
            if name != "mimetype"
        }


def preview_hashes(result: RenderResult) -> dict[str, str]:
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    return {
        relative: digest
        for relative, digest in manifest["files"].items()
        if relative not in {"mimetype"}
    }


def invalidate_premium_render(edition, reason: str) -> None:
    renderer = EditionRenderer(edition)
    renderer.build_dir.mkdir(parents=True, exist_ok=True)
    state = renderer._load_state()
    reasons = list(state.get("invalidation_reasons") or [])
    if reason not in reasons:
        reasons.append(reason)
    state.update(
        {
            "schema": "gaiden_premium_render_state_v1",
            "theme": THEME_NAME,
            "approved_fingerprint": "",
            "status": "EDITION_RENDER_REQUIRED",
            "invalidation_reasons": reasons,
        }
    )
    renderer._write_state(state)
