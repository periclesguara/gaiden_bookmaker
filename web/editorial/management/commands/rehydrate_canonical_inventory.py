from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re

from django.core.management.base import BaseCommand
from django.db import transaction

from collections_module.models import Collection, CollectionArtifact, CollectionItem, CollectionRunState
from editorial.models import (
    Contributor,
    ContributorRole,
    Edition,
    EditionPipeline,
    EditionText,
    Language,
    PipelineStage,
    Seal,
    Work,
)


TITLE_PLACEHOLDERS = {"", "front", "front page", "collection"}
LANG_PRIORITY = ("en", "pt-br", "es", "de", "it", "fr")


@dataclass
class BookRecord:
    code: str
    language: str
    title: str
    author: str
    raw_source_path: str
    cover_filepath: str
    current_stage: str


class Command(BaseCommand):
    help = "Reidrata a base principal a partir do storage canonico (23 livros + 1 collection real)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--purge-existing",
            action="store_true",
            help="Remove works/editions/collections atuais antes de reidratar.",
        )

    def handle(self, *args, **options):
        root = Path(__file__).resolve().parents[5]
        data_dir = root / "data"

        books = discover_books(data_dir)
        collections = discover_collections(data_dir)

        with transaction.atomic():
            if options["purge_existing"]:
                CollectionRunState.objects.all().delete()
                CollectionArtifact.objects.all().delete()
                CollectionItem.objects.all().delete()
                Collection.objects.all().delete()
                EditionText.objects.all().delete()
                EditionPipeline.objects.all().delete()
                Edition.objects.all().delete()
                Work.objects.all().delete()

            ensure_seed_objects()
            imported_books = [upsert_book(book) for book in books]
            imported_collections = [upsert_collection(meta) for meta in collections]

        self.stdout.write(
            self.style.SUCCESS(
                f"OK: base principal reidratada com {len(imported_books)} livros e {len(imported_collections)} collection."
            )
        )
        self.stdout.write(", ".join(book.code for book in imported_books))
        self.stdout.write(", ".join(col.code for col in imported_collections) or "sem collections")


def ensure_seed_objects() -> None:
    for code in LANG_PRIORITY:
        Language.objects.get_or_create(code=code, defaults={"name": code, "native_name": code})
    Seal.objects.get_or_create(slug="default", defaults={"name": "default"})
    Seal.objects.get_or_create(slug="mantaquest", defaults={"name": "MantaQuest"})


def discover_books(data_dir: Path) -> list[BookRecord]:
    builds_dir = data_dir / "builds"
    raw_dir = data_dir / "raw"
    frontmatter_dir = data_dir / "frontmatter"

    build_codes = {p.name for p in builds_dir.glob("book_*") if p.is_dir()}
    raw_codes = {p.name for p in raw_dir.glob("book_*") if p.is_dir()}
    frontmatter_codes = {p.name for p in frontmatter_dir.glob("book_*") if p.is_dir()}
    selected_codes = sorted(build_codes | ((raw_codes & frontmatter_codes) - build_codes))

    records: list[BookRecord] = []
    for code in selected_codes:
        language = detect_book_language(data_dir, code)
        title, author = extract_book_metadata(data_dir, code, language)
        raw_source_path = detect_raw_source(data_dir, code)
        cover_filepath = detect_cover_path(data_dir, code, language)
        current_stage = detect_stage(data_dir, code, language, raw_source_path)
        records.append(
            BookRecord(
                code=code,
                language=language,
                title=title or code,
                author=author or "Unknown",
                raw_source_path=raw_source_path,
                cover_filepath=cover_filepath,
                current_stage=current_stage,
            )
        )
    return records


def discover_collections(data_dir: Path) -> list[dict]:
    manifests: list[dict] = []
    collections_dir = data_dir / "collections"
    for collection_dir in sorted(collections_dir.glob("collection_*")):
        if "_test_" in collection_dir.name:
            continue
        for manifest_path in collection_dir.glob("*/manifest.json"):
            data = json.loads(manifest_path.read_text())
            title = str(data.get("title") or "").strip()
            if normalize_text(title) in TITLE_PLACEHOLDERS:
                continue
            item_count = int(data.get("item_count") or 0)
            status = str(data.get("status") or "")
            if item_count < 2:
                continue
            if status not in {
                "COLLECTION_MERGED",
                "COLLECTION_READY_FOR_PIPELINE",
                "COLLECTION_PIPELINE_RUNNING",
                "COLLECTION_DONE",
            }:
                continue
            data["_manifest_path"] = str(manifest_path)
            manifests.append(data)
    manifests.sort(key=lambda item: item["collection_code"])
    return manifests


def upsert_book(book: BookRecord) -> BookRecord:
    language = Language.objects.get(code=book.language)
    seal = Seal.objects.get(slug="mantaquest")
    author = Contributor.objects.get_or_create(
        name=book.author,
        defaults={"role": ContributorRole.AUTHOR},
    )[0]
    work = Work.objects.create(
        code=book.code,
        title=book.title,
        original_language=language,
        author=author,
        publisher="RinoBooks",
        is_public_domain=True,
    )
    edition = Edition.objects.create(
        work=work,
        language=language,
        seal=seal,
        main_contributor=author,
        publisher="RinoBooks",
        raw_source_path=book.raw_source_path,
        title=book.title,
        author=book.author,
        cover_filepath=book.cover_filepath,
        language_code=book.language,
        seal_name="MantaQuest",
        imprint_name="RinoBooks",
        country="Brazil",
    )
    EditionPipeline.objects.create(
        edition=edition,
        current_stage=book.current_stage,
        raw_at=edition.created_at if book.raw_source_path else None,
    )
    EditionText.objects.create(
        edition=edition,
        raw_path=book.raw_source_path,
    )
    return book


def upsert_collection(meta: dict) -> Collection:
    collection = Collection.objects.create(
        code=meta["collection_code"],
        title=meta.get("title", ""),
        subtitle=meta.get("subtitle", ""),
        collection_kind=meta.get("collection_kind", "mixed_collection"),
        author_display_name=first_author(meta) or "Unknown",
        language=meta.get("language", "en"),
        status=meta.get("status", "COLLECTION_CREATED"),
        item_count=int(meta.get("item_count") or 0),
    )
    for item in meta.get("items", []):
        CollectionItem.objects.create(
            collection=collection,
            order_index=int(item.get("order_index") or 0),
            author_name=item.get("author_name", ""),
            work_title=item.get("work_title", ""),
            source_filename=item.get("source_filename", ""),
            source_original_path=item.get("source_original_path", ""),
            upload_status=item.get("upload_status", "pending"),
            prep_status=item.get("prep_status", "pending"),
            normalize_status=item.get("normalize_status", "pending"),
            merge_status=item.get("merge_status", "pending"),
            is_active=True,
        )
        for artifact_key, artifact_type in (("prepared_output", "prepared"), ("normalized_output", "normalized_item")):
            artifact = item.get(artifact_key) or {}
            if artifact.get("path"):
                CollectionArtifact.objects.create(
                    collection=collection,
                    artifact_type=f"{artifact_type}_{int(item.get('order_index') or 0):02d}",
                    language=collection.language,
                    path=artifact["path"],
                    sha256=artifact.get("sha256", ""),
                )

    merged = meta.get("merged_final") or {}
    if merged.get("path"):
        CollectionArtifact.objects.create(
            collection=collection,
            artifact_type="merged_final",
            language=collection.language,
            path=merged["path"],
            sha256=merged.get("sha256", ""),
        )

    CollectionRunState.objects.create(
        collection=collection,
        current_step=meta.get("status", ""),
        last_error="",
        is_locked=meta.get("status") == "COLLECTION_PIPELINE_RUNNING",
    )
    return collection


def detect_book_language(data_dir: Path, code: str) -> str:
    candidates = set()
    for root_name in ("builds", "frontmatter", "translated", "images", "covers"):
        root = data_dir / root_name / code
        if root.exists():
            for child in root.iterdir():
                if child.is_dir():
                    candidates.add(child.name)
    for lang in LANG_PRIORITY:
        if lang in candidates:
            return lang
    return sorted(candidates)[0] if candidates else "en"


def extract_book_metadata(data_dir: Path, code: str, language: str) -> tuple[str, str]:
    frontispiece = data_dir / "frontmatter" / code / language / "frontispiece.md"
    title = ""
    author = ""
    if frontispiece.exists():
        for raw_line in frontispiece.read_text(errors="ignore").splitlines():
            line = clean_line(raw_line)
            if not line or line.startswith("#"):
                continue
            low = normalize_text(line)
            if not title:
                if line.lower().startswith("title:"):
                    candidate = line.split(":", 1)[1].strip().strip(";")
                    if normalize_text(candidate) not in TITLE_PLACEHOLDERS:
                        title = candidate
                        continue
                if low not in TITLE_PLACEHOLDERS and not low.startswith("by ") and not low.startswith("author:"):
                    title = line
                    continue
            if not author:
                if line.lower().startswith("author:"):
                    author = line.split(":", 1)[1].strip().strip(";")
                elif line.lower().startswith("by "):
                    author = line[3:].strip().strip(";")
    return title, author


def detect_raw_source(data_dir: Path, code: str) -> str:
    raw_root = data_dir / "raw" / code
    if not raw_root.exists():
        return ""
    candidates = sorted(p for p in raw_root.iterdir() if p.is_file())
    return str(candidates[0]) if candidates else ""


def detect_cover_path(data_dir: Path, code: str, language: str) -> str:
    for suffix in ("cover.jpg", "cover.jpeg", "cover.png"):
        candidate = data_dir / "covers" / code / language / suffix
        if candidate.exists():
            return str(candidate.relative_to(data_dir.parent))
    return ""


def detect_stage(data_dir: Path, code: str, language: str, raw_source_path: str) -> str:
    build_dir = data_dir / "builds" / code / language
    if (build_dir / "merge_polidor.txt").exists():
        return PipelineStage.POLISHED
    if (build_dir / "merge_refine.txt").exists():
        return PipelineStage.REFINED
    if (build_dir / "merge_translate.txt").exists():
        return PipelineStage.TRANSLATED
    if (data_dir / "chunks" / code).exists():
        return PipelineStage.CHUNKED
    if raw_source_path:
        return PipelineStage.RAW
    return PipelineStage.RAW


def first_author(meta: dict) -> str:
    items = meta.get("items") or []
    return str(items[0].get("author_name") or "").strip() if items else ""


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def clean_line(value: str) -> str:
    line = (value or "").strip().strip(";")
    line = re.sub(r"[*_`#]+", "", line).strip()
    if line.startswith(":::"):
        return ""
    return line
