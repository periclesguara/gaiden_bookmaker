from __future__ import annotations

import logging
import mimetypes
import os
import re
import time
import zipfile
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction

from gaiden.application.author_studio.dto import SourceIngestionResult
from gaiden.application.author_studio.extract_core_text import apply_core_text_policy, identify_source_provider
from gaiden.domain.author_studio.codes import generate_canonical_code, generate_source_code
from gaiden.domain.author_studio.enums import CanonicalTextStatus, SourceStatus, WorkStatus
from gaiden.domain.author_studio.exceptions import DuplicateSourceError, InvalidSourceError
from gaiden.infrastructure.author_studio.checksum import calculate_sha256
from gaiden.infrastructure.author_studio.extractor_registry import default_registry

logger = logging.getLogger(__name__)
ALLOWED_EXTENSIONS = {".epub", ".txt", ".md", ".markdown", ".html", ".htm", ".xhtml", ".xml", ".docx", ".odt", ".rtf", ".pdf", ".mobi", ".azw", ".azw3", ".fb2", ".cbz", ".zip"}
ZIP_EXTENSIONS = {".epub", ".docx", ".odt", ".azw3", ".cbz", ".zip"}
MIME_EXTENSIONS = {
    "text/plain": {".txt", ".md", ".markdown"},
    "text/markdown": {".md", ".markdown"},
    "text/html": {".html", ".htm"},
    "application/xhtml+xml": {".xhtml"},
    "application/xml": {".xml", ".fb2"},
    "text/xml": {".xml", ".fb2"},
    "application/pdf": {".pdf"},
    "application/rtf": {".rtf"},
    "text/rtf": {".rtf"},
    "application/epub+zip": {".epub"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
    "application/vnd.oasis.opendocument.text": {".odt"},
    "application/zip": {".zip", ".cbz", ".epub", ".docx", ".odt", ".azw3"},
    "application/x-zip-compressed": {".zip", ".cbz", ".epub", ".docx", ".odt", ".azw3"},
}


def max_upload_bytes() -> int:
    try:
        megabytes = int(os.environ.get("GAIDEN_AUTHOR_STUDIO_MAX_UPLOAD_MB", "500"))
    except ValueError:
        megabytes = 500
    return max(megabytes, 1) * 1024 * 1024


def _signature_is_valid(upload, extension: str) -> bool:
    upload.seek(0)
    header = upload.read(80)
    upload.seek(0)
    if extension in ZIP_EXTENSIONS:
        try:
            with zipfile.ZipFile(upload) as archive:
                bad = archive.testzip()
                names = archive.namelist()
                total_uncompressed = sum(info.file_size for info in archive.infolist())
                if (
                    bad
                    or not names
                    or len(names) > 10_000
                    or total_uncompressed > max_upload_bytes() * 20
                    or any(name.startswith(("/", "../")) or "/../" in name for name in names)
                ):
                    return False
        except (zipfile.BadZipFile, OSError):
            return False
        finally:
            upload.seek(0)
        return True
    if extension == ".pdf":
        return header.startswith(b"%PDF-")
    if extension == ".rtf":
        return header.lstrip().startswith(b"{\\rtf")
    if extension in {".mobi", ".azw"}:
        return len(header) >= 68 and header[60:68] in {b"BOOKMOBI", b"TEXtREAd"}
    if extension in {".html", ".htm", ".xhtml", ".xml", ".fb2"}:
        return header.lstrip().startswith(b"<")
    return b"\x00" not in header


def validate_upload(upload) -> tuple[str, str, int]:
    filename = Path(upload.name or "").name
    extension = Path(filename).suffix.lower()
    size = int(getattr(upload, "size", 0) or 0)
    if not size:
        raise InvalidSourceError("O arquivo enviado está vazio.")
    if size > max_upload_bytes():
        raise InvalidSourceError("O arquivo excede o limite configurado para upload.")
    mime_type = (getattr(upload, "content_type", "") or mimetypes.guess_type(filename)[0] or "").lower()
    if not extension:
        safe_candidates = MIME_EXTENSIONS.get(mime_type, set())
        if len(safe_candidates) != 1:
            raise InvalidSourceError("Não foi possível identificar com segurança o formato do arquivo sem extensão.")
        extension = next(iter(safe_candidates))
    if extension not in ALLOWED_EXTENSIONS:
        raise InvalidSourceError(f"Formato não suportado: {extension}.")
    mime_candidates = MIME_EXTENSIONS.get(mime_type)
    if mime_candidates and extension not in mime_candidates:
        raise InvalidSourceError("O MIME type não corresponde à extensão informada.")
    if not _signature_is_valid(upload, extension):
        message = "Arquivo ZIP malformado ou potencialmente perigoso." if extension in ZIP_EXTENSIONS else "A assinatura do arquivo não corresponde ao formato informado."
        raise InvalidSourceError(message)
    return extension, mime_type, size


def store_work_source(*, work, upload):
    from author_studio.models import Work, WorkSource

    extension, mime_type, size = validate_upload(upload)
    digest = calculate_sha256(upload)
    with transaction.atomic():
        locked_work = Work.objects.select_for_update().get(pk=work.pk)
        if WorkSource.objects.filter(work=locked_work, sha256=digest).exists():
            logger.warning("source_duplicate_detected work_id=%s code=%s sha256=%s", work.pk, work.code, digest)
            raise DuplicateSourceError("Este arquivo já foi enviado para esta obra.")
        sequences = []
        prefix = re.escape(f"{locked_work.code}-SRC")
        for code in WorkSource.objects.filter(work=locked_work).values_list("code", flat=True):
            match = re.fullmatch(prefix + r"(\d+)", code)
            if match:
                sequences.append(int(match.group(1)))
        code = generate_source_code(locked_work.code, max(sequences, default=0) + 1)
        upload.seek(0)
        try:
            source = WorkSource.objects.create(
                work=locked_work,
                code=code,
                original_filename=Path(upload.name).name,
                stored_file=upload,
                extension=extension,
                mime_type=mime_type,
                size_bytes=size,
                sha256=digest,
                source_provider="UNKNOWN",
                extraction_status=SourceStatus.STORED,
            )
        except IntegrityError as exc:
            raise DuplicateSourceError("Este arquivo já foi enviado para esta obra.") from exc
        locked_work.status = WorkStatus.SOURCE_UPLOADED
        locked_work.save(update_fields=["status", "updated_at"])
    logger.info("source_uploaded id=%s code=%s work_id=%s size=%s extension=%s status=%s", source.pk, source.code, work.pk, size, extension, source.extraction_status)
    return source


def extract_source_text(source, registry=None) -> str | None:
    registry = registry or default_registry()
    extractor = registry.get_extractor(source.extension, source.mime_type)
    if extractor is None:
        source.extraction_status = SourceStatus.UNSUPPORTED_EXTRACTION
        source.save(update_fields=["extraction_status"])
        source.work.status = WorkStatus.NEEDS_REVIEW
        source.work.save(update_fields=["status", "updated_at"])
        return None
    source.extraction_status = SourceStatus.EXTRACTING
    source.extraction_error = ""
    source.save(update_fields=["extraction_status", "extraction_error"])
    source.work.status = WorkStatus.EXTRACTING
    source.work.save(update_fields=["status", "updated_at"])
    started = time.monotonic()
    logger.info("extraction_started source_id=%s code=%s extension=%s", source.pk, source.code, source.extension)
    try:
        text = extractor.extract(Path(source.stored_file.path))
        if not text or not text.strip():
            raise ValueError("Nenhum conteúdo textual pôde ser extraído.")
    except Exception as exc:
        source.extraction_status = SourceStatus.FAILED
        source.extraction_error = str(exc)[:2000]
        source.save(update_fields=["extraction_status", "extraction_error"])
        source.work.status = WorkStatus.FAILED
        source.work.save(update_fields=["status", "updated_at"])
        logger.exception("extraction_failed source_id=%s code=%s exception=%s", source.pk, source.code, type(exc).__name__)
        return None
    source.extraction_status = SourceStatus.EXTRACTED
    source.source_provider = identify_source_provider(text)
    source.save(update_fields=["extraction_status", "source_provider"])
    source.work.status = WorkStatus.EXTRACTED
    source.work.save(update_fields=["status", "updated_at"])
    logger.info("extraction_completed source_id=%s code=%s duration_ms=%s status=%s", source.pk, source.code, round((time.monotonic() - started) * 1000), source.extraction_status)
    return text


def create_canonical_text(*, source, extracted_text: str):
    from author_studio.models import CanonicalText

    result = apply_core_text_policy(extracted_text)
    if not result.text:
        return None
    status = CanonicalTextStatus.NEEDS_REVIEW if result.needs_review else CanonicalTextStatus.READY
    payload = result.text.rstrip() + "\n"
    digest = __import__("hashlib").sha256(payload.encode("utf-8")).hexdigest()
    with transaction.atomic():
        canonical, _ = CanonicalText.objects.select_for_update().get_or_create(
            work=source.work,
            defaults={"source": source, "code": generate_canonical_code(source.work.code), "sha256": digest},
        )
        canonical.source = source
        canonical.sha256 = digest
        canonical.character_count = len(result.text)
        canonical.word_count = len(re.findall(r"\b\w+\b", result.text, flags=re.UNICODE))
        canonical.status = status
        canonical.text_file.save("canonical.txt", ContentFile(payload.encode("utf-8")), save=False)
        canonical.save()
        source.work.status = WorkStatus.NEEDS_REVIEW if result.needs_review else WorkStatus.CANONICAL_READY
        source.work.save(update_fields=["status", "updated_at"])
    event = "canonical_text_needs_review" if result.needs_review else "canonical_text_created"
    logger.info("%s id=%s code=%s source_id=%s words=%s characters=%s status=%s", event, canonical.pk, canonical.code, source.pk, canonical.word_count, canonical.character_count, canonical.status)
    return canonical


def ingest_work_source(*, work, upload) -> SourceIngestionResult:
    source = store_work_source(work=work, upload=upload)
    extracted = extract_source_text(source)
    canonical = create_canonical_text(source=source, extracted_text=extracted) if extracted else None
    return SourceIngestionResult(work=source.work, source=source, canonical_text=canonical)


def ingest_new_work(*, author, title: str, original_language: str, upload) -> SourceIngestionResult:
    """Atomically create the work and persist its immutable original source."""
    from gaiden.application.author_studio.create_work import create_work

    with transaction.atomic():
        work = create_work(author=author, title=title, original_language=original_language)
        source = store_work_source(work=work, upload=upload)
    extracted = extract_source_text(source)
    canonical = create_canonical_text(source=source, extracted_text=extracted) if extracted else None
    return SourceIngestionResult(work=work, source=source, canonical_text=canonical)
