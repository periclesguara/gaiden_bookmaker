from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from editorial.models import (
    Contributor,
    ContributorRole,
    Edition,
    Language,
    Seal,
    Work,
)
from pipeline.services.pipeline_stage_sync import sync_pipeline_stage


PROJECT_ROOT = Path(__file__).resolve().parents[4]
BOOK_ID_RE = re.compile(r"^book_\d{4}$", re.IGNORECASE)
LANG_RE = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$", re.IGNORECASE)
AUTHOR_BY_RE = re.compile(r"^\s*by\s+(.+?)\s*$", re.IGNORECASE)
AUTHOR_PUBLISHED_RE = re.compile(r"originally published .* by (.+?)\.\s*$", re.IGNORECASE)
IMPRINT_RE = re.compile(r"imprint of\s+([^.]+)\.?", re.IGNORECASE)
TITLE_NOISE_PREFIXES = (
    "chapter ",
    "book ",
    "copyright",
    "about this edition",
    "frontispiece",
)
TITLE_NOISE_EXACT = {"by", "illustrated edition", "modern english", "modern edition"}


def _resolve_root(raw_path: str) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


def _is_book_id(value: str) -> bool:
    return bool(BOOK_ID_RE.match((value or "").strip()))


def _is_lang_code(value: str) -> bool:
    return bool(LANG_RE.match((value or "").strip()))


def _normalize_book_id(value: str) -> str:
    return (value or "").strip().lower()


def _normalize_lang(value: str) -> str:
    return (value or "").strip().lower()


def _looks_like_title(text: str) -> bool:
    clean = text.strip()
    if not clean:
        return False
    low = clean.lower()
    if low in TITLE_NOISE_EXACT:
        return False
    if any(low.startswith(prefix) for prefix in TITLE_NOISE_PREFIXES):
        return False
    if clean.startswith("©"):
        return False
    if clean.isdigit():
        return False
    return True


def _extract_metadata_from_text(raw_text: str) -> dict[str, str]:
    title = ""
    author = ""
    imprint = ""

    lines = raw_text.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        heading = stripped.lstrip("#").strip() if stripped.startswith("#") else ""
        if not title and heading and _looks_like_title(heading):
            title = heading

        normalized_line = stripped.lstrip("#").strip()

        if not title and ":" in normalized_line and _looks_like_title(normalized_line):
            # Prefer explicit "Conan ...: The Hour of the Dragon" style lines.
            title = normalized_line

        if not title and _looks_like_title(normalized_line) and len(normalized_line) <= 120:
            title = normalized_line

        if not author:
            by_match = AUTHOR_BY_RE.match(stripped)
            if by_match:
                author = by_match.group(1).strip()
            elif stripped.lower() == "by":
                # Frontispiece format: "By" in one line and author on next line.
                for nxt in lines[idx + 1 : idx + 4]:
                    nxt_clean = nxt.strip()
                    if nxt_clean:
                        author = nxt_clean
                        break

        if not author:
            published_match = AUTHOR_PUBLISHED_RE.search(stripped)
            if published_match:
                author = published_match.group(1).strip()

        if not imprint:
            imprint_match = IMPRINT_RE.search(stripped)
            if imprint_match:
                imprint = imprint_match.group(1).strip()

        if title and author and imprint:
            break

    return {"title": title, "author": author, "imprint": imprint}


def _extract_metadata_from_files(paths: list[Path]) -> dict[str, str]:
    merged = {"title": "", "author": "", "imprint": ""}
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        candidate = _extract_metadata_from_text(text[:40000])
        for key in merged:
            if not merged[key] and candidate.get(key):
                merged[key] = candidate[key]
    return merged


def _candidate_metadata_files(book_id: str, lang: str, books_root: Path, builds_root: Path, frontmatter_root: Path) -> list[Path]:
    candidates = [
        frontmatter_root / book_id / lang / "copyright.md",
        frontmatter_root / book_id / lang / "about_edition.md",
        frontmatter_root / book_id / lang / "frontispiece.md",
        books_root / book_id / lang / f"{book_id}_refine_clean.md",
        books_root / book_id / lang / f"{book_id}_refine_clean.txt",
        books_root / book_id / lang / "canonical.md",
        builds_root / book_id / lang / "book.en.v03.build.md",
        builds_root / book_id / lang / "book.en.v01.build.md",
        builds_root / book_id / lang / "book.en.v03.kdp_merged.md",
        builds_root / book_id / lang / "book.en.v01.kdp_merged.md",
        builds_root / book_id / lang / "merge_refine_clean.md",
    ]
    return [p for p in candidates if p.exists()]


def _index_book_langs(scan_roots: list[Path]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for root in scan_roots:
        if not root.exists():
            continue
        for dirpath, _, filenames in os.walk(root):
            path = Path(dirpath)
            parts = [part.lower() for part in path.parts]
            if not filenames and not any(Path(dirpath).iterdir()):
                # Keep empty language dirs as evidence too.
                pass
            for i, part in enumerate(parts):
                if not _is_book_id(part):
                    continue
                if i + 1 < len(parts) and _is_lang_code(parts[i + 1]):
                    index[part].add(_normalize_lang(parts[i + 1]))
    return index


def _detect_books(scan_roots: list[Path]) -> list[str]:
    books = set()
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("book_????"):
            if path.is_dir() and _is_book_id(path.name):
                books.add(_normalize_book_id(path.name))
    return sorted(books)


def _default_language_names(lang_code: str) -> tuple[str, str]:
    names = {
        "en": ("English", "English"),
        "pt-br": ("Portuguese (Brazil)", "Português (Brasil)"),
        "es": ("Spanish", "Español"),
        "de": ("German", "Deutsch"),
        "fr": ("French", "Français"),
        "it": ("Italian", "Italiano"),
    }
    return names.get(lang_code, (lang_code, lang_code))


def _ensure_language(lang_code: str) -> Language:
    name, native = _default_language_names(lang_code)
    language, _ = Language.objects.get_or_create(
        code=lang_code,
        defaults={"name": name, "native_name": native, "is_active": True},
    )
    return language


def _ensure_default_seal() -> Seal:
    return Seal.objects.get_or_create(
        slug="mantaquest",
        defaults={"name": "MantaQuest", "description": "", "is_active": True},
    )[0]


def _raw_source_candidate(book_id: str, lang: str, scan_roots: list[Path]) -> str:
    allowed = {"source.txt", "source.md"}
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.name.lower() not in allowed:
                continue
            parts = [part.lower() for part in path.parts]
            for i, part in enumerate(parts):
                if part == book_id and i + 1 < len(parts) and parts[i + 1] == lang:
                    return str(path.resolve())
    return ""


class Command(BaseCommand):
    help = "Rehydrate Work/Edition/EditionPipeline from filesystem artifacts."

    def add_arguments(self, parser):
        parser.add_argument("--books-root", default="data/books")
        parser.add_argument("--builds-root", default="data/builds")
        parser.add_argument("--frontmatter-root", default="gaiden/frontmatter_store")
        parser.add_argument(
            "--extra-roots",
            default="data/raw,data/normalized,data/chunks,data/translated",
            help="Comma-separated additional roots to scan for book/lang evidence.",
        )
        parser.add_argument("--langs", default="en")
        parser.add_argument("--only", default="", help="Comma list, e.g. book_0003,book_0004")
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **opts):
        books_root = _resolve_root(opts["books_root"])
        builds_root = _resolve_root(opts["builds_root"])
        frontmatter_root = _resolve_root(opts["frontmatter_root"])

        extra_roots: list[Path] = []
        for raw in (opts.get("extra_roots") or "").split(","):
            raw_clean = raw.strip()
            if raw_clean:
                extra_roots.append(_resolve_root(raw_clean))

        scan_roots = [books_root, builds_root, frontmatter_root, *extra_roots]
        langs_filter = [_normalize_lang(x) for x in opts["langs"].split(",") if x.strip()]
        only = [_normalize_book_id(x) for x in opts["only"].split(",") if x.strip()]
        dry_run = bool(opts["dry_run"])

        indexed = _index_book_langs(scan_roots)
        books = only or _detect_books(scan_roots)

        self.stdout.write(f"books_root={books_root}")
        self.stdout.write(f"builds_root={builds_root}")
        self.stdout.write(f"frontmatter_root={frontmatter_root}")
        self.stdout.write(f"scan_roots={[str(x) for x in scan_roots]}")
        self.stdout.write(f"langs_filter={langs_filter}")
        self.stdout.write(f"books={books}")
        self.stdout.write(f"dry_run={dry_run}")

        for book_id in books:
            if not _is_book_id(book_id):
                self.stdout.write(self.style.WARNING(f"skip invalid book id: {book_id}"))
                continue

            detected_langs = indexed.get(book_id, set())
            target_langs = sorted(set(langs_filter) & detected_langs) if langs_filter else sorted(detected_langs)
            if not target_langs:
                # If caller passed --only and there is no folder evidence, still allow explicit lang.
                target_langs = list(langs_filter or [])

            if not target_langs:
                self.stdout.write(self.style.WARNING(f"skip {book_id}: no language evidence found"))
                continue

            for lang in target_langs:
                if not _is_lang_code(lang):
                    self.stdout.write(self.style.WARNING(f"skip {book_id}/{lang}: invalid language code"))
                    continue

                metadata_files = _candidate_metadata_files(book_id, lang, books_root, builds_root, frontmatter_root)
                metadata = _extract_metadata_from_files(metadata_files)
                source_path = _raw_source_candidate(book_id, lang, scan_roots)

                work_title = metadata["title"] or book_id
                author_name = metadata["author"] or "Unknown Author"
                imprint = metadata["imprint"] or "RinoBooks"

                if dry_run:
                    self.stdout.write(
                        f"[DRY] {book_id}/{lang} title={work_title!r} author={author_name!r} "
                        f"imprint={imprint!r} source={'yes' if source_path else 'no'}"
                    )
                    continue

                language = _ensure_language(lang)
                author = Contributor.objects.get_or_create(
                    name=author_name,
                    defaults={"role": ContributorRole.AUTHOR},
                )[0]
                seal = _ensure_default_seal()

                work, work_created = Work.objects.get_or_create(
                    code=book_id,
                    defaults={
                        "title": work_title,
                        "original_language": language,
                        "author": author,
                        "publisher": imprint,
                        "source_format": "TXT",
                    },
                )

                work_fields: list[str] = []
                if work.author_id != author.id and (work.author.name or "").strip().lower() == "unknown author":
                    work.author = author
                    work_fields.append("author")
                if not (work.title or "").strip() or work.title == work.code:
                    if work_title:
                        work.title = work_title
                        work_fields.append("title")
                if not (work.publisher or "").strip() and imprint:
                    work.publisher = imprint
                    work_fields.append("publisher")
                if not work.original_language_id:
                    work.original_language = language
                    work_fields.append("original_language")
                if work_fields:
                    work.save(update_fields=sorted(set(work_fields)))

                edition, created = Edition.objects.get_or_create(
                    work=work,
                    language=language,
                    seal=seal,
                    defaults={
                        "status": Edition.STATUS_REGISTERED,
                        "book_id": book_id,
                        "lang": lang,
                        "title": work_title,
                        "author": author_name,
                        "imprint_name": "RinoBooks",
                        "seal_name": "MantaQuest",
                        "publisher": imprint,
                        "language_code": lang,
                        "raw_source_path": source_path,
                        "raw_materialized_path": source_path,
                    },
                )

                changed_fields: list[str] = []
                if not (edition.book_id or "").strip():
                    edition.book_id = book_id
                    changed_fields.append("book_id")
                if not (edition.lang or "").strip():
                    edition.lang = lang
                    changed_fields.append("lang")
                if not (edition.title or "").strip() and work_title:
                    edition.title = work_title
                    changed_fields.append("title")
                if not (edition.author or "").strip() and author_name:
                    edition.author = author_name
                    changed_fields.append("author")
                if not (edition.publisher or "").strip() and imprint:
                    edition.publisher = imprint
                    changed_fields.append("publisher")
                if not (edition.language_code or "").strip():
                    edition.language_code = lang
                    changed_fields.append("language_code")
                if source_path and not (edition.raw_source_path or "").strip():
                    edition.raw_source_path = source_path
                    changed_fields.append("raw_source_path")
                if source_path and not (edition.raw_materialized_path or "").strip():
                    edition.raw_materialized_path = source_path
                    changed_fields.append("raw_materialized_path")
                if changed_fields:
                    edition.save(update_fields=sorted(set(changed_fields + ["updated_at"])))

                sync_pipeline_stage(edition)
                fm_dir = frontmatter_root / book_id / lang
                self.stdout.write(
                    f"OK {book_id}/{lang} created={created} work_created={work_created} "
                    f"fm_dir={'YES' if fm_dir.exists() else 'NO'}"
                )
