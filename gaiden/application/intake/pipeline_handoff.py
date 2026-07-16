from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from django.db import connection, transaction
from django.utils import timezone
from django.utils.text import slugify

from editorial.models import (
    Contributor,
    Edition,
    EditionPipeline,
    EditionText,
    Language,
    PipelineStage,
    Seal,
    Work,
)
from gaiden.domain.intake import IntakeState
from gaiden.application.pipeline.source_extract import run_source_extract
from gaiden.infrastructure import intake_storage, storage
from gaiden.infrastructure.source_extractors.base import canonical_paths
from pipeline.models import BookEditionTemplate
from pipeline.services.utils import normalize_lang
from web.intake_module.models import IntakeItem


class IntakeHandoffError(ValueError):
    pass


class IntakeHandoffConflict(IntakeHandoffError):
    pass


@dataclass(frozen=True)
class HandoffResult:
    edition: Edition
    pipeline: EditionPipeline
    raw_path: Path
    translated_path: Path
    created_files: tuple[Path, ...]


@dataclass(frozen=True)
class BookmakerHandoffResult:
    edition: Edition
    pipeline: EditionPipeline
    source_original_path: Path
    canonical_text_path: Path
    created: bool


LANGUAGE_NAMES = {
    "en": "English",
    "de": "Deutsch",
    "es": "Español",
    "fr": "Français",
    "it": "Italiano",
    "ptbr": "Português (Brasil)",
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validated_artifact(path_value: str, *, label: str) -> tuple[Path, bytes, str]:
    if not path_value:
        raise IntakeHandoffError(f"{label} path is required")
    path = intake_storage.resolve_stored_path(path_value)
    if path.is_symlink() or not path.is_file():
        raise IntakeHandoffError(f"{label} file is missing or invalid")
    payload = path.read_bytes()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IntakeHandoffError(f"{label} must be valid UTF-8") from exc
    if not text.strip():
        raise IntakeHandoffError(f"{label} cannot be empty")
    return path, payload, _sha256(payload)


def _validate_item(item: IntakeItem) -> tuple[bytes, str, bytes, str, str, str]:
    if item.status != IntakeState.READY_FOR_EDITING.value:
        raise IntakeHandoffError("Item must be READY_FOR_EDITING before pipeline handoff")
    required = {
        "author": item.batch.author_default,
        "confirmed_title": item.confirmed_title,
        "original_year": item.original_year,
        "book_code": item.book_code,
        "target_language": item.target_language,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise IntakeHandoffError(f"Required intake fields are missing: {', '.join(missing)}")
    if item.original_year < 1 or item.original_year > timezone.now().year:
        raise IntakeHandoffError("original_year must be a valid confirmed year")

    _clean_source, clean_payload, clean_hash = _validated_artifact(item.clean_path, label="clean.txt")
    _translation_source, translated_payload, translated_hash = _validated_artifact(
        item.translation_return_path,
        label="translation return",
    )
    source_language = normalize_lang(item.batch.source_language)
    target_language = normalize_lang(item.target_language)
    if not source_language or not target_language:
        raise IntakeHandoffError("Source and target languages are required")
    return clean_payload, clean_hash, translated_payload, translated_hash, source_language, target_language


def _atomic_copy_payload(destination: Path, payload: bytes, expected_hash: str) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        if destination.is_file() and not destination.is_symlink() and _sha256(destination.read_bytes()) == expected_hash:
            return False
        raise IntakeHandoffConflict(f"Canonical artifact already exists with different content: {destination}")

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if _sha256(destination.read_bytes()) == expected_hash:
                return False
            raise IntakeHandoffConflict(
                f"Canonical artifact appeared with different content: {destination}"
            )
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _language(code: str) -> Language:
    label = LANGUAGE_NAMES.get(code, code)
    language, _ = Language.objects.get_or_create(
        code=code,
        defaults={"name": label, "native_name": label},
    )
    return language


def _assert_work_compatible(
    work: Work,
    *,
    title: str,
    year: int,
    source_language: Language,
    author_name: str,
) -> None:
    conflicts = []
    if work.title.strip().casefold() != title.strip().casefold():
        conflicts.append("title")
    if work.year is not None and work.year != year:
        conflicts.append("original_year")
    if work.original_language_id != source_language.id:
        conflicts.append("source_language")
    if work.author.name.strip().casefold() != author_name.strip().casefold():
        conflicts.append("author")
    if conflicts:
        raise IntakeHandoffConflict(
            f"book_code {work.code!r} already belongs to a different work ({', '.join(conflicts)})"
        )


def _get_or_create_pipeline(edition: Edition) -> tuple[EditionPipeline, bool]:
    existing = EditionPipeline.objects.filter(edition=edition).first()
    if existing is not None:
        return existing, False

    table = EditionPipeline._meta.db_table
    model_columns = {field.column for field in EditionPipeline._meta.local_fields}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
              AND is_nullable = 'NO'
              AND column_default IS NULL
            """,
            [table],
        )
        unknown_required = [row for row in cursor.fetchall() if row[0] not in model_columns]
    if not unknown_required:
        return EditionPipeline.objects.create(edition=edition), True

    instance = EditionPipeline(edition=edition)
    values = {}
    for field in EditionPipeline._meta.local_fields:
        if field.primary_key:
            continue
        values[field.column] = getattr(instance, field.attname)
    for column_name, data_type in unknown_required:
        if data_type == "boolean":
            values[column_name] = False
        elif data_type in {"integer", "smallint", "bigint", "numeric"}:
            values[column_name] = 0
        elif data_type in {"timestamp with time zone", "timestamp without time zone", "date"}:
            values[column_name] = timezone.now()
        else:
            values[column_name] = ""
    quote = connection.ops.quote_name
    columns = list(values)
    placeholders = ", ".join(["%s"] * len(columns))
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {quote(table)} ({', '.join(quote(name) for name in columns)}) "
            f"VALUES ({placeholders}) RETURNING {quote('id')}",
            [values[name] for name in columns],
        )
        pipeline_id = cursor.fetchone()[0]
    return EditionPipeline.objects.get(pk=pipeline_id), True


def _persist_handoff(
    item: IntakeItem,
    *,
    raw_path: Path,
    translated_path: Path,
    clean_payload: bytes,
    clean_hash: str,
    translated_payload: bytes,
    translated_hash: str,
    source_language_code: str,
    target_language_code: str,
) -> tuple[Edition, EditionPipeline]:
    source_language = _language(source_language_code)
    target_language = _language(target_language_code)
    author_name = (item.batch.author_default or "Unknown Author").strip()
    author, _ = Contributor.objects.get_or_create(name=author_name, defaults={"role": "AUTHOR"})

    work = Work.objects.select_for_update().filter(code=item.book_code).first()
    if work is not None:
        _assert_work_compatible(
            work,
            title=item.confirmed_title,
            year=item.original_year,
            source_language=source_language,
            author_name=author_name,
        )
    else:
        work = Work.objects.create(
            code=item.book_code,
            title=item.confirmed_title,
            original_language=source_language,
            author=author,
            publisher=item.batch.imprint_default,
            year=item.original_year,
            is_public_domain=item.batch.public_domain,
        )

    template = BookEditionTemplate.objects.select_for_update().filter(
        book_code=item.book_code,
        language=target_language_code,
    ).first()
    if template is not None and (
        template.title.strip().casefold() != item.confirmed_title.strip().casefold()
        or template.author_name.strip().casefold() != author_name.casefold()
    ):
        raise IntakeHandoffConflict(
            f"book_code {item.book_code!r} and language {target_language_code!r} already have another template"
        )
    if template is None:
        template = BookEditionTemplate(
            book_code=item.book_code,
            language=target_language_code,
            title=item.confirmed_title,
            author_name=author_name,
            publication_year=timezone.now().year,
        )
    template.original_publication_date = date(item.original_year, 1, 1)
    template.work_kind = (
        BookEditionTemplate.WORK_KIND_PUBLIC_DOMAIN
        if item.batch.public_domain
        else BookEditionTemplate.WORK_KIND_AUTHORIAL
    )
    template.imprint_name = item.batch.imprint_default
    template.collection_name = item.batch.collection_name
    template.editor_name = item.batch.editor_default
    template.registration_status = BookEditionTemplate.STATUS_READY_FOR_BLOCK_02
    template.source_file_type = "txt"
    template.source_original_name = item.source_filename
    template.source_saved_path = str(raw_path)
    template.source_file_size = len(clean_payload)
    template.source_uploaded_at = timezone.now()
    template.source_file_sha256 = clean_hash
    template.save(apply_defaults=False)

    seal_name = "MantaQuest"
    seal, _ = Seal.objects.get_or_create(
        slug=slugify(seal_name),
        defaults={"name": seal_name},
    )
    edition = Edition.objects.select_for_update().filter(
        work=work,
        language=target_language,
    ).first()
    if edition is None:
        edition = Edition.objects.create(
            work=work,
            language=target_language,
            seal=seal,
        )
    edition.title = item.confirmed_title
    edition.author = author_name
    edition.publisher = item.batch.imprint_default
    edition.edition_year = timezone.now().year
    edition.publication_year = timezone.now().year
    edition.editor = item.batch.editor_default
    edition.imprint_name = item.batch.imprint_default or edition.imprint_name
    edition.language_code = target_language_code
    edition.raw_source_path = str(raw_path)
    edition.save()

    EditionText.objects.update_or_create(
        edition=edition,
        defaults={
            "raw_text": clean_payload.decode("utf-8"),
            "raw_path": str(raw_path),
        },
    )
    now = timezone.now()
    pipeline, _ = _get_or_create_pipeline(edition)
    if pipeline.current_stage not in {PipelineStage.RAW, PipelineStage.TRANSLATED}:
        raise IntakeHandoffConflict(
            f"Edition pipeline is already beyond the intake handoff stage: {pipeline.current_stage}"
        )
    pipeline.current_stage = PipelineStage.TRANSLATED
    pipeline.core_last_txt_path = str(translated_path)
    pipeline.translation_language = target_language_code
    pipeline.md_language = target_language_code
    pipeline.raw_at = pipeline.raw_at or now
    pipeline.translated_at = pipeline.translated_at or now
    pipeline.last_log = (
        f"{now.isoformat()} :: TRANSLATED :: intake handoff item={item.id} "
        f"raw_sha256={clean_hash} translated_sha256={translated_hash}"
    )
    pipeline.save(
        update_fields=[
            "current_stage",
            "core_last_txt_path",
            "translation_language",
            "md_language",
            "raw_at",
            "translated_at",
            "last_log",
        ]
    )

    item.handoff_raw_path = str(raw_path)
    item.handoff_translated_path = str(translated_path)
    item.handoff_raw_sha256 = clean_hash
    item.handoff_translated_sha256 = translated_hash
    item.handoff_edition_id = edition.id
    item.handed_off_at = item.handed_off_at or now
    item.save(
        update_fields=[
            "handoff_raw_path",
            "handoff_translated_path",
            "handoff_raw_sha256",
            "handoff_translated_sha256",
            "handoff_edition_id",
            "handed_off_at",
            "updated_at",
        ]
    )
    return edition, pipeline


def handoff_to_pipeline(item: IntakeItem) -> HandoffResult:
    created_files: list[Path] = []
    try:
        with transaction.atomic():
            locked_item = IntakeItem.objects.select_for_update().select_related("batch").get(pk=item.pk)
            (
                clean_payload,
                clean_hash,
                translated_payload,
                translated_hash,
                source_language,
                target_language,
            ) = _validate_item(locked_item)
            raw_path = storage.raw_source_path(locked_item.book_code, source_language, ".txt")
            translated_path = (
                storage.translated_dir(locked_item.book_code, target_language)
                / f"clean_translate_{target_language}.txt"
            )
            if _atomic_copy_payload(raw_path, clean_payload, clean_hash):
                created_files.append(raw_path)
            if _atomic_copy_payload(translated_path, translated_payload, translated_hash):
                created_files.append(translated_path)
            edition, pipeline = _persist_handoff(
                locked_item,
                raw_path=raw_path,
                translated_path=translated_path,
                clean_payload=clean_payload,
                clean_hash=clean_hash,
                translated_payload=translated_payload,
                translated_hash=translated_hash,
                source_language_code=source_language,
                target_language_code=target_language,
            )
        return HandoffResult(
            edition=edition,
            pipeline=pipeline,
            raw_path=raw_path,
            translated_path=translated_path,
            created_files=tuple(created_files),
        )
    except Exception:
        for path in reversed(created_files):
            path.unlink(missing_ok=True)
        raise


def _validate_bookmaker_item(item: IntakeItem) -> tuple[Path, str, str, str]:
    if item.status not in {
        IntakeState.DOWNLOADED.value,
        IntakeState.CLEAN_READY.value,
    }:
        raise IntakeHandoffError(
            "Item must be DOWNLOADED or CLEAN_READY before opening in Gaiden Bookmaker"
        )
    if item.duplicate_of_id:
        raise IntakeHandoffError(f"Duplicate item must use canonical item {item.duplicate_of_id}")
    required = {
        "confirmed_title": item.confirmed_title,
        "original_year": item.original_year,
        "book_code": item.book_code,
        "source_language": item.batch.source_language,
        "target_language": item.target_language,
        "original_path": item.original_path,
        "source_sha256": item.source_sha256,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise IntakeHandoffError(f"Required intake fields are missing: {', '.join(missing)}")
    if item.original_year < 1 or item.original_year > timezone.now().year:
        raise IntakeHandoffError("original_year must be a valid confirmed year")
    source = intake_storage.resolve_stored_path(item.original_path)
    if source.is_symlink() or not source.is_file():
        raise IntakeHandoffError("Original intake file is missing or invalid")
    source_hash = _sha256(source.read_bytes())
    if source_hash != item.source_sha256:
        raise IntakeHandoffConflict("Original intake SHA-256 does not match the database")
    source_language = normalize_lang(item.batch.source_language)
    target_language = normalize_lang(item.target_language)
    if not source_language or not target_language:
        raise IntakeHandoffError("Source and target languages are required")
    return source, source_hash, source_language, target_language


def _existing_bookmaker_handoff(item: IntakeItem) -> BookmakerHandoffResult | None:
    if not item.handoff_edition_id:
        return None
    edition = (
        Edition.objects.select_related("work", "language")
        .filter(pk=item.handoff_edition_id)
        .first()
    )
    if edition is None or edition.work.code != item.book_code:
        raise IntakeHandoffConflict("Stored IntakeItem edition link is invalid")
    pipeline, _ = _get_or_create_pipeline(edition)
    source_template = BookEditionTemplate.objects.filter(
        book_code=item.book_code,
        language=edition.language.code,
    ).first()
    source_original_path = Path(source_template.source_saved_path) if source_template else Path()
    canonical_text_path = Path(edition.raw_source_path) if edition.raw_source_path else Path()
    return BookmakerHandoffResult(
        edition=edition,
        pipeline=pipeline,
        source_original_path=source_original_path,
        canonical_text_path=canonical_text_path,
        created=False,
    )


def _bookmaker_records(
    item: IntakeItem,
    *,
    source_hash: str,
    source_language_code: str,
    target_language_code: str,
    source_original_path: Path,
    canonical_text_path: Path,
    canonical_text: str,
) -> tuple[Edition, EditionPipeline]:
    source_language = _language(source_language_code)
    target_language = _language(target_language_code)
    author_name = (item.batch.author_default or "Unknown Author").strip()
    author, _ = Contributor.objects.get_or_create(name=author_name, defaults={"role": "AUTHOR"})

    work = Work.objects.select_for_update().filter(code=item.book_code).first()
    if work is None:
        work = Work.objects.create(
            code=item.book_code,
            title=item.confirmed_title,
            original_language=source_language,
            author=author,
            publisher=item.batch.imprint_default,
            year=item.original_year,
            is_public_domain=item.batch.public_domain,
        )
    else:
        _assert_work_compatible(
            work,
            title=item.confirmed_title,
            year=item.original_year,
            source_language=source_language,
            author_name=author_name,
        )

    template = BookEditionTemplate.objects.select_for_update().filter(
        book_code=item.book_code,
        language=target_language_code,
    ).first()
    if template is not None and (
        template.title.strip().casefold() != item.confirmed_title.strip().casefold()
        or template.author_name.strip().casefold() != author_name.casefold()
    ):
        raise IntakeHandoffConflict(
            f"book_code {item.book_code!r} and language {target_language_code!r} already have another template"
        )
    if template is None:
        template = BookEditionTemplate(
            book_code=item.book_code,
            language=target_language_code,
            title=item.confirmed_title,
            author_name=author_name,
            publication_year=timezone.now().year,
        )
    template.original_publication_date = date(item.original_year, 1, 1)
    template.work_kind = (
        BookEditionTemplate.WORK_KIND_PUBLIC_DOMAIN
        if item.batch.public_domain
        else BookEditionTemplate.WORK_KIND_AUTHORIAL
    )
    template.imprint_name = item.batch.imprint_default
    template.collection_name = item.batch.collection_name
    template.editor_name = item.batch.editor_default
    template.registration_status = BookEditionTemplate.STATUS_READY_FOR_BLOCK_02
    template.text_source_mode = Path(item.source_filename).suffix.lower().lstrip(".")
    template.source_file_type = template.text_source_mode
    template.source_original_name = item.source_filename
    template.source_saved_path = str(source_original_path)
    template.source_file_size = item.source_size
    template.source_uploaded_at = template.source_uploaded_at or timezone.now()
    template.source_file_sha256 = source_hash
    template.save(apply_defaults=False)

    seal_name = "MantaQuest"
    seal, _ = Seal.objects.get_or_create(slug=slugify(seal_name), defaults={"name": seal_name})
    edition = Edition.objects.select_for_update().filter(
        work=work,
        language=target_language,
    ).first()
    if edition is None:
        edition = Edition.objects.create(work=work, language=target_language, seal=seal)
    edition.title = item.confirmed_title
    edition.author = author_name
    edition.main_contributor = author
    edition.publisher = item.batch.imprint_default
    edition.editor = item.batch.editor_default
    edition.imprint_name = item.batch.imprint_default or edition.imprint_name
    edition.edition_year = timezone.now().year
    edition.publication_year = timezone.now().year
    edition.language_code = target_language_code
    edition.raw_source_path = str(canonical_text_path)
    edition.save()

    EditionText.objects.update_or_create(
        edition=edition,
        defaults={"raw_text": canonical_text, "raw_path": str(canonical_text_path)},
    )
    now = timezone.now()
    pipeline, _ = _get_or_create_pipeline(edition)
    if pipeline.current_stage not in {PipelineStage.RAW, "SOURCE_EXTRACTED"}:
        raise IntakeHandoffConflict(
            f"Edition pipeline is already beyond source ingestion: {pipeline.current_stage}"
        )
    pipeline.current_stage = "SOURCE_EXTRACTED"
    pipeline.translation_language = (item.target_language or "").strip().lower().replace("-", "_")
    pipeline.raw_at = pipeline.raw_at or now
    pipeline.last_log = (
        f"{now.isoformat()} :: SOURCE_EXTRACTED :: intake item={item.id} "
        f"source_sha256={source_hash}"
    )
    pipeline.save(
        update_fields=["current_stage", "translation_language", "raw_at", "last_log"]
    )

    item.handoff_raw_path = str(source_original_path)
    item.handoff_raw_sha256 = source_hash
    item.handoff_edition_id = edition.id
    item.handed_off_at = item.handed_off_at or now
    item.save(
        update_fields=[
            "handoff_raw_path",
            "handoff_raw_sha256",
            "handoff_edition_id",
            "handed_off_at",
            "updated_at",
        ]
    )
    return edition, pipeline


def open_in_bookmaker(item: IntakeItem) -> BookmakerHandoffResult:
    with transaction.atomic():
        locked_item = (
            IntakeItem.objects.select_for_update()
            .select_related("batch")
            .get(pk=item.pk)
        )
        source, source_hash, source_language, target_language = _validate_bookmaker_item(locked_item)
        existing = _existing_bookmaker_handoff(locked_item)
        if existing is not None:
            return existing
        source_paths = canonical_paths(
            locked_item.book_code,
            target_language,
            source.suffix,
        )
        if source_paths.original_file.exists() or source_paths.original_file.is_symlink():
            if (
                source_paths.original_file.is_symlink()
                or not source_paths.original_file.is_file()
                or _sha256(source_paths.original_file.read_bytes()) != source_hash
            ):
                raise IntakeHandoffConflict("Canonical RAW original already contains different content")
        extract_result = run_source_extract(locked_item.book_code, target_language, source)
        source_original_path = storage.resolve_repo_path(extract_result["original_file"])
        canonical_text_path = storage.resolve_repo_path(extract_result["canonical_txt"])
        canonical_text = canonical_text_path.read_text(encoding="utf-8")
        edition, pipeline = _bookmaker_records(
            locked_item,
            source_hash=source_hash,
            source_language_code=source_language,
            target_language_code=target_language,
            source_original_path=source_original_path,
            canonical_text_path=canonical_text_path,
            canonical_text=canonical_text,
        )
        return BookmakerHandoffResult(
            edition=edition,
            pipeline=pipeline,
            source_original_path=source_original_path,
            canonical_text_path=canonical_text_path,
            created=True,
        )
