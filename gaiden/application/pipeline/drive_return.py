from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from django.apps import apps
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from gaiden.application.pipeline import official_body
from gaiden.domain.languages import canonical_language, internal_language
from gaiden.infrastructure import intake_storage, storage
from gaiden.infrastructure.intake_drive import DriveFile, RcloneClient


TRANSLATION_JOBS_ROOT = "04_TRANSLATION_JOBS"
MANIFEST_SCHEMA = "gaiden_translation_job_v2"
VALIDATION_SCHEMA = "gaiden_translation_return_validation_v2"
PASS = "PASS"
WARNING = "WARNING_REQUIRES_CONFIRMATION"
FAIL = "FAIL"


class DriveReturnError(RuntimeError):
    pass


@dataclass(frozen=True)
class DriveReturnLink:
    job_id: str
    item_id: int
    edition_id: int
    folder: str
    book_code: str
    title_slug: str
    target_language: str
    output_stage: str
    canonical_filename: str
    manifest_filename: str
    input_sha256: str


@dataclass(frozen=True)
class DriveReturnResult:
    pending_path: Path
    remote_filename: str
    sha256: str
    size: int
    validation_status: str
    job_id: str


@dataclass(frozen=True)
class PendingDriveReturn:
    pending_path: Path
    remote_filename: str
    remote_folder: str
    target_language: str
    output_stage: str
    sha256: str
    size: int
    job_id: str
    validation_status: str


@dataclass(frozen=True)
class TranslationDriveExportResult:
    source_path: Path
    remote_path: str
    manifest_remote_path: str
    return_folder: str
    return_filename: str
    manifest_filename: str
    job_id: str
    no_op: bool


OfficialBodyResult = official_body.OfficialBodyResult


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _title_slug(value: str) -> str:
    normalized = slugify(value or "").replace("-", "_")
    if not normalized:
        raise DriveReturnError("The confirmed title is required")
    return normalized


def _linked_item(edition, *, for_update: bool = False):
    IntakeItem = apps.get_model("intake_module", "IntakeItem")
    queryset = IntakeItem.objects.select_related("batch").filter(
        handoff_edition_id=edition.id,
        duplicate_of__isnull=True,
    )
    if for_update:
        queryset = queryset.select_for_update()
    items = list(queryset[:2])
    if len(items) != 1:
        raise DriveReturnError("Edition must have exactly one explicit IntakeItem link")
    item = items[0]
    if (item.book_code or "").strip() != edition.work.code:
        raise DriveReturnError("The Intake book code does not match this edition")
    return item


def _target_language(edition, item) -> str:
    EditionPipeline = apps.get_model("editorial", "EditionPipeline")
    configured = (
        EditionPipeline.objects.filter(edition_id=edition.id)
        .values_list("translation_language", flat=True)
        .first()
        or item.target_language
        or edition.language.code
    )
    target = canonical_language(configured)
    if canonical_language(item.target_language) != target:
        raise DriveReturnError("The Intake target language does not match this edition")
    return target


def _source_language(item) -> str:
    return canonical_language(item.batch.source_language)


def _manifest_path(job) -> Path:
    return storage.data_dir() / "translation_jobs" / str(job.job_id) / "manifest.json"


def _validation_report_path(job) -> Path:
    return storage.data_dir() / "translation_jobs" / str(job.job_id) / "validation_report.json"


def _relative_storage_path(path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(storage.data_dir().resolve()).as_posix()
    except ValueError as exc:
        raise DriveReturnError("Translation job path escapes the storage root") from exc


def _job_manifest(job) -> dict:
    return {
        "schema": MANIFEST_SCHEMA,
        "schema_version": 2,
        "job_id": str(job.job_id),
        "book_code": job.edition.work.code,
        "edition_id": job.edition_id,
        "intake_item_id": job.intake_item_id,
        "source_language": job.source_language,
        "target_language": job.target_language,
        "output_stage": job.output_stage,
        "confirmed_title": job.confirmed_title,
        "frozen_title_slug": job.frozen_title_slug,
        "input_filename": job.input_filename,
        "input_sha256": job.input_sha256,
        "expected_return_folder": job.expected_return_folder,
        "expected_return_filename": job.expected_return_filename,
        "manifest_filename": job.manifest_filename,
        "required_external_stages": (
            ["translation", "refine", "polish"] if job.output_stage == "official" else ["translation"]
        ),
        "created_at": job.created_at.isoformat(),
    }


def _job_link(job) -> DriveReturnLink:
    return DriveReturnLink(
        job_id=str(job.job_id),
        item_id=job.intake_item_id,
        edition_id=job.edition_id,
        folder=job.expected_return_folder,
        book_code=job.edition.work.code,
        title_slug=job.frozen_title_slug,
        target_language=job.target_language,
        output_stage=job.output_stage,
        canonical_filename=job.expected_return_filename,
        manifest_filename=job.manifest_filename,
        input_sha256=job.input_sha256,
    )


def _active_job(edition, *, output_stage: str | None = None, for_update: bool = False):
    TranslationJob = apps.get_model("intake_module", "TranslationJob")
    queryset = TranslationJob.objects.select_related("edition__work", "intake_item").filter(
        edition_id=edition.id,
    ).exclude(status=TranslationJob.STATUS_SUPERSEDED)
    if output_stage:
        queryset = queryset.filter(output_stage=output_stage)
    if for_update:
        queryset = queryset.select_for_update()
    jobs = list(queryset.order_by("-created_at", "-id")[:2])
    if not jobs:
        raise DriveReturnError("No active TranslationJob exists for this edition")
    if len(jobs) > 1 and jobs[0].output_stage == jobs[1].output_stage:
        raise DriveReturnError("Multiple active TranslationJobs are ambiguous")
    return jobs[0]


def resolve_drive_return_link(
    edition,
    *,
    require_normalized: bool = True,
    output_stage: str | None = None,
) -> DriveReturnLink:
    del require_normalized  # retained for compatibility; the immutable input SHA is authoritative.
    return _job_link(_active_job(edition, output_stage=output_stage))


def translation_input_folder(edition) -> str:
    try:
        return _active_job(edition).input_folder
    except DriveReturnError:
        item = _linked_item(edition)
        return f"{edition.work.code}/{_target_language(edition, item)}/input"


def translation_input_filename(edition) -> str:
    try:
        return _active_job(edition).input_filename
    except DriveReturnError:
        item = _linked_item(edition)
        target = _target_language(edition, item)
        return f"{edition.work.code}_{_title_slug(item.confirmed_title)}_heading_clean_{target}.txt"


def preview_translation_job(edition, *, output_stage: str = "translated") -> DriveReturnLink:
    TranslationJob = apps.get_model("intake_module", "TranslationJob")
    if output_stage not in {TranslationJob.STAGE_TRANSLATED, TranslationJob.STAGE_OFFICIAL}:
        raise DriveReturnError("output_stage must be translated or official")
    try:
        return _job_link(_active_job(edition, output_stage=output_stage))
    except DriveReturnError:
        item = _linked_item(edition)
        target = _target_language(edition, item)
        title_slug = _title_slug(item.confirmed_title)
        book_code = edition.work.code
        return DriveReturnLink(
            job_id="",
            item_id=item.id,
            edition_id=edition.id,
            folder=f"{book_code}/{target}/return",
            book_code=book_code,
            title_slug=title_slug,
            target_language=target,
            output_stage=output_stage,
            canonical_filename=f"{book_code}_{title_slug}_{output_stage}_{target}.txt",
            manifest_filename="created-at-export.json",
            input_sha256="",
        )


def _create_or_reuse_job(edition, payload: bytes, *, output_stage: str):
    TranslationJob = apps.get_model("intake_module", "TranslationJob")
    if output_stage not in {TranslationJob.STAGE_TRANSLATED, TranslationJob.STAGE_OFFICIAL}:
        raise DriveReturnError("output_stage must be translated or official")
    digest = _sha256(payload)
    with transaction.atomic():
        locked_edition = type(edition).objects.select_for_update().select_related("work", "language").get(
            pk=edition.pk
        )
        item = _linked_item(locked_edition, for_update=True)
        target = _target_language(locked_edition, item)
        source = _source_language(item)
        title = (item.confirmed_title or "").strip()
        title_slug = _title_slug(title)
        existing = TranslationJob.objects.select_for_update().filter(
            edition=locked_edition,
            intake_item=item,
            target_language=target,
            output_stage=output_stage,
            input_sha256=digest,
        ).first()
        if existing is not None:
            if existing.status == TranslationJob.STATUS_SUPERSEDED:
                existing.status = TranslationJob.STATUS_EXPORTED
                existing.superseded_at = None
                existing.save(update_fields=["status", "superseded_at", "updated_at"])
            return existing, True, []

        previous = list(
            TranslationJob.objects.select_for_update().filter(
                edition=locked_edition,
                target_language=target,
                output_stage=output_stage,
            ).exclude(status=TranslationJob.STATUS_SUPERSEDED)
        )
        now = timezone.now()
        for job in previous:
            job.status = TranslationJob.STATUS_SUPERSEDED
            job.superseded_at = now
            job.save(update_fields=["status", "superseded_at", "updated_at"])

        input_folder = f"{locked_edition.work.code}/{target}/input"
        return_folder = f"{locked_edition.work.code}/{target}/return"
        input_filename = (
            f"{locked_edition.work.code}_{title_slug}_heading_clean_{target}.txt"
        )
        return_filename = (
            f"{locked_edition.work.code}_{title_slug}_{output_stage}_{target}.txt"
        )
        job = TranslationJob.objects.create(
            edition=locked_edition,
            intake_item=item,
            source_language=source,
            target_language=target,
            output_stage=output_stage,
            confirmed_title=title,
            frozen_title_slug=title_slug,
            input_folder=input_folder,
            input_filename=input_filename,
            input_sha256=digest,
            expected_return_folder=return_folder,
            expected_return_filename=return_filename,
            manifest_filename="pending",
            manifest_path="pending",
        )
        job.manifest_filename = f"translation_job_{job.job_id}.json"
        manifest_path = _manifest_path(job)
        job.manifest_path = _relative_storage_path(manifest_path)
        job.save(update_fields=["manifest_filename", "manifest_path", "updated_at"])
        intake_storage.atomic_write_json(manifest_path, _job_manifest(job))
        return job, False, previous


def _archive_previous_inputs(client, previous_jobs) -> None:
    if not previous_jobs or not hasattr(client, "archive_file"):
        return
    for prior in previous_jobs:
        archive_folder = f"{prior.edition.work.code}/{prior.target_language}/superseded/{prior.job_id}/input"
        client.archive_file(prior.input_folder, prior.input_filename, archive_folder)
        client.archive_file(prior.input_folder, prior.manifest_filename, archive_folder)


def export_translation_job(
    edition,
    *,
    client=None,
    output_stage: str = "translated",
) -> TranslationDriveExportResult:
    item = _linked_item(edition)
    source_path = storage.heading_cleaner_dir(item.book_code) / "clean.txt"
    if source_path.is_symlink() or not source_path.is_file():
        raise DriveReturnError("HeadingCleaner output is not available")
    try:
        payload = source_path.read_bytes()
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DriveReturnError("HeadingCleaner output must be UTF-8") from exc
    if not text.strip():
        raise DriveReturnError("HeadingCleaner output is empty")

    job, reused, previous = _create_or_reuse_job(edition, payload, output_stage=output_stage)
    drive_client = client or RcloneClient(inbox=TRANSLATION_JOBS_ROOT)
    try:
        drive_client.check_available()
        _archive_previous_inputs(drive_client, previous)
        source_result = drive_client.upload_file_to_path(
            source_path,
            job.input_folder,
            job.input_filename,
        )
        manifest_result = drive_client.upload_file_to_path(
            _manifest_path(job),
            job.input_folder,
            job.manifest_filename,
        )
    except Exception as exc:
        type(job).objects.filter(pk=job.pk).update(
            status=type(job).STATUS_FAILED,
            updated_at=timezone.now(),
        )
        raise DriveReturnError("TranslationJob export failed") from exc
    if job.status == type(job).STATUS_FAILED:
        type(job).objects.filter(pk=job.pk).update(
            status=type(job).STATUS_EXPORTED,
            updated_at=timezone.now(),
        )
    return TranslationDriveExportResult(
        source_path=source_path,
        remote_path=source_result["remote_path"],
        manifest_remote_path=manifest_result["remote_path"],
        return_folder=job.expected_return_folder,
        return_filename=job.expected_return_filename,
        manifest_filename=job.manifest_filename,
        job_id=str(job.job_id),
        no_op=reused and bool(source_result["no_op"]) and bool(manifest_result["no_op"]),
    )


def _direct_files(files: list[DriveFile]) -> list[DriveFile]:
    direct = []
    for drive_file in files:
        relative = PurePosixPath(drive_file.relative_path)
        if relative.is_absolute() or ".." in relative.parts or relative.parent != PurePosixPath("."):
            continue
        if Path(drive_file.name).name != drive_file.name:
            continue
        direct.append(drive_file)
    return direct


def _select_exact(files: list[DriveFile], filename: str, *, label: str) -> DriveFile:
    matches = [row for row in _direct_files(files) if row.name == filename]
    if len(matches) != 1:
        raise DriveReturnError(f"Exactly one {label} is required in the canonical return folder")
    return matches[0]


def _download(client, folder: str, drive_file: DriveFile, destination: Path) -> bytes:
    client.download_file(folder, drive_file, destination)
    if destination.is_symlink() or not destination.is_file():
        raise DriveReturnError("Drive return did not produce a regular file")
    return destination.read_bytes()


def _validate_return_manifest(job, payload: bytes) -> dict:
    try:
        manifest = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DriveReturnError("Return manifest must be valid UTF-8 JSON") from exc
    expected = _job_manifest(job)
    immutable_fields = [
        "schema", "schema_version", "job_id", "book_code", "edition_id",
        "intake_item_id", "source_language", "target_language", "output_stage",
        "confirmed_title", "frozen_title_slug", "input_filename", "input_sha256",
        "expected_return_folder", "expected_return_filename", "manifest_filename",
    ]
    mismatches = [field for field in immutable_fields if manifest.get(field) != expected.get(field)]
    if mismatches:
        raise DriveReturnError(f"Return manifest identity mismatch: {', '.join(mismatches)}")
    if job.output_stage == "official":
        completed = set(manifest.get("completed_stages") or [])
        required = {"translation", "refine", "polish"}
        if not required.issubset(completed):
            raise DriveReturnError("Official return manifest must confirm translation, refine, and polish")
    return manifest


HEADING_RE = re.compile(r"^(?:#{1,6}\s+.+|(?:chapter|book|part)\s+[0-9ivxlcdm]+\b.*)$", re.I | re.M)
PROTECTED_RE = re.compile(r"(?:\{\{[^{}]+\}\}|\[\[[^\[\]]+\]\]|id=[\"'][^\"']+[\"']|<a\s+name=[\"'][^\"']+[\"'])")
LANGUAGE_MARKERS = {
    "en": {"the", "and", "of", "to", "in", "that"},
    "pt": {"de", "que", "e", "o", "a", "para"},
    "fr": {"de", "la", "le", "et", "les", "des"},
    "de": {"der", "die", "das", "und", "von", "zu"},
    "es": {"de", "la", "el", "y", "que", "los"},
    "it": {"di", "la", "il", "e", "che", "per"},
}


def _detected_language(text: str) -> str | None:
    words = re.findall(r"[a-zà-ÿ]+", text.casefold())
    if len(words) < 40:
        return None
    scores = {lang: sum(word in markers for word in words) for lang, markers in LANGUAGE_MARKERS.items()}
    best, score = max(scores.items(), key=lambda row: row[1])
    return best if score >= 3 else None


def _return_validation(job, input_payload: bytes, return_payload: bytes) -> dict:
    failures: list[str] = []
    warnings: list[str] = []
    try:
        text = return_payload.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
        failures.append("invalid_utf8")
    if not text.strip():
        failures.append("empty_content")
    if _sha256(return_payload) == job.input_sha256:
        failures.append("identical_to_input")

    minimum = max(1, int(os.environ.get("GAIDEN_RETURN_MIN_BYTES", "100")))
    if len(return_payload) < minimum:
        failures.append("below_minimum_size")
    ratio = len(return_payload) / max(1, len(input_payload))
    min_ratio = float(os.environ.get("GAIDEN_RETURN_WARN_MIN_RATIO", "0.50"))
    max_ratio = float(os.environ.get("GAIDEN_RETURN_WARN_MAX_RATIO", "2.00"))
    if ratio < min_ratio or ratio > max_ratio:
        warnings.append("size_ratio_outside_expected_range")

    try:
        input_text = input_payload.decode("utf-8")
    except UnicodeDecodeError:
        input_text = ""
        failures.append("stored_input_invalid_utf8")
    input_headings = [match.group(0).strip() for match in HEADING_RE.finditer(input_text)]
    return_headings = [match.group(0).strip() for match in HEADING_RE.finditer(text)]
    if len(input_headings) != len(return_headings):
        warnings.append("heading_count_changed")
    input_tokens = set(PROTECTED_RE.findall(input_text))
    return_tokens = set(PROTECTED_RE.findall(text))
    if not input_tokens.issubset(return_tokens):
        failures.append("protected_markers_missing")

    detected = _detected_language(text)
    expected_base = job.target_language.split("-", 1)[0]
    if detected is not None and detected != expected_base:
        failures.append("detected_language_mismatch")

    status = FAIL if failures else WARNING if warnings else PASS
    return {
        "schema": VALIDATION_SCHEMA,
        "job_id": str(job.job_id),
        "edition_id": job.edition_id,
        "intake_item_id": job.intake_item_id,
        "book_code": job.edition.work.code,
        "target_language": job.target_language,
        "output_stage": job.output_stage,
        "input_sha256": job.input_sha256,
        "return_sha256": _sha256(return_payload),
        "input_size": len(input_payload),
        "return_size": len(return_payload),
        "size_ratio": ratio,
        "detected_language": detected,
        "input_heading_count": len(input_headings),
        "return_heading_count": len(return_headings),
        "failures": failures,
        "warnings": warnings,
        "status": status,
        "validated_at": timezone.now().isoformat(),
    }


def pending_path(edition, job=None) -> Path:
    job = job or _active_job(edition)
    return storage.editions_dir(edition.id) / "core" / "drive_returns" / str(job.job_id) / "pending.txt"


def pending_metadata_path(edition, job=None) -> Path:
    job = job or _active_job(edition)
    return pending_path(edition, job).with_name("pending.json")


def miolo_oficial_path(edition) -> Path:
    return official_body.canonical_path(edition)


def read_pending_return(edition, *, job=None) -> PendingDriveReturn | None:
    try:
        job = job or _active_job(edition)
    except DriveReturnError:
        return None
    text_path = pending_path(edition, job)
    metadata_path = pending_metadata_path(edition, job)
    if any(path.is_symlink() or not path.is_file() for path in (text_path, metadata_path)):
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload = text_path.read_bytes()
        pending = PendingDriveReturn(
            pending_path=text_path,
            remote_filename=str(metadata["remote_filename"]),
            remote_folder=str(metadata["remote_folder"]),
            target_language=str(metadata["target_language"]),
            output_stage=str(metadata["output_stage"]),
            sha256=str(metadata["sha256"]),
            size=int(metadata["size"]),
            job_id=str(metadata["job_id"]),
            validation_status=str(metadata["validation_status"]),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if pending.sha256 != _sha256(payload) or pending.size != len(payload):
        return None
    return pending


def validated_pending_payload(edition, *, output_stage: str | None = None):
    job = _active_job(edition, output_stage=output_stage)
    pending = read_pending_return(edition, job=job)
    if pending is None:
        raise DriveReturnError("No validated Drive return is awaiting processing")
    link = _job_link(job)
    if (
        pending.job_id != str(job.job_id)
        or pending.remote_filename != link.canonical_filename
        or pending.remote_folder != link.folder
        or pending.target_language != link.target_language
        or pending.output_stage != link.output_stage
    ):
        raise DriveReturnError("Pending Drive return metadata does not match the frozen job")
    warning_confirmed = bool(
        pending.validation_status == WARNING
        and job.warning_confirmed_at
        and job.warning_confirmed_by
        and job.warning_confirmation_note
    )
    if pending.validation_status != PASS and not warning_confirmed:
        raise DriveReturnError(
            "Only PASS is automatic; WARNING_REQUIRES_CONFIRMATION needs an audited editorial confirmation"
        )
    payload = pending.pending_path.read_bytes()
    if _sha256(payload) != job.return_sha256:
        raise DriveReturnError("Pending return SHA does not match the TranslationJob")
    if job.return_sha256 == job.input_sha256:
        raise DriveReturnError("Pending return is identical to the frozen input")
    return payload, pending, job


def import_drive_return(
    edition,
    *,
    client=None,
    output_stage: str | None = None,
) -> DriveReturnResult:
    job = _active_job(edition, output_stage=output_stage)
    link = _job_link(job)
    folder_parts = PurePosixPath(link.folder).parts
    if folder_parts != (link.book_code, link.target_language, "return"):
        raise DriveReturnError("Drive return path is not canonical")

    drive_client = client or RcloneClient(inbox=TRANSLATION_JOBS_ROOT)
    drive_client.check_available()
    files = drive_client.list_files(link.folder)
    selected = _select_exact(files, link.canonical_filename, label="return TXT")
    manifest_file = _select_exact(files, link.manifest_filename, label="return manifest")

    with tempfile.TemporaryDirectory(prefix="gaiden-drive-return-") as temporary_dir:
        temporary = Path(temporary_dir)
        manifest_payload = _download(
            drive_client, link.folder, manifest_file, temporary / link.manifest_filename
        )
        _validate_return_manifest(job, manifest_payload)
        return_payload = _download(
            drive_client, link.folder, selected, temporary / link.canonical_filename
        )

    input_path = storage.heading_cleaner_dir(link.book_code) / "clean.txt"
    if input_path.is_symlink() or not input_path.is_file():
        raise DriveReturnError("Frozen HeadingCleaner input is unavailable")
    input_payload = input_path.read_bytes()
    if _sha256(input_payload) != job.input_sha256:
        raise DriveReturnError("Frozen HeadingCleaner input SHA mismatch")
    report = _return_validation(job, input_payload, return_payload)
    report_path = _validation_report_path(job)
    intake_storage.atomic_write_json(report_path, report, overwrite=True)
    type(job).objects.filter(pk=job.pk).update(
        status=(
            type(job).STATUS_RETURN_PENDING
            if report["status"] in {PASS, WARNING}
            else type(job).STATUS_FAILED
        ),
        return_sha256=report["return_sha256"],
        validation_status=report["status"],
        validation_report_path=_relative_storage_path(report_path),
        warning_confirmed_at=None,
        warning_confirmed_by="",
        warning_confirmation_note="",
        updated_at=timezone.now(),
    )
    if report["status"] == FAIL:
        reasons = report["failures"] or report["warnings"]
        raise DriveReturnError(
            f"Drive return validation result: {report['status']} ({', '.join(reasons)})"
        )

    destination = pending_path(edition, job)
    metadata = pending_metadata_path(edition, job)
    intake_storage.atomic_write_bytes(destination, return_payload, overwrite=True)
    intake_storage.atomic_write_json(
        metadata,
        {
            "schema": "gaiden_drive_return_pending_v2",
            "job_id": str(job.job_id),
            "remote_root": TRANSLATION_JOBS_ROOT,
            "remote_folder": link.folder,
            "remote_filename": link.canonical_filename,
            "target_language": link.target_language,
            "output_stage": link.output_stage,
            "sha256": report["return_sha256"],
            "size": len(return_payload),
            "validation_status": report["status"],
            "validation_report_path": _relative_storage_path(report_path),
        },
        overwrite=True,
    )
    return DriveReturnResult(
        destination,
        link.canonical_filename,
        report["return_sha256"],
        len(return_payload),
        report["status"],
        str(job.job_id),
    )


def confirm_warning(
    edition,
    *,
    actor: str,
    note: str,
    output_stage: str | None = None,
):
    actor = str(actor).strip()
    note = str(note).strip()
    if not actor or not note:
        raise DriveReturnError("Editorial warning confirmation requires actor and justification")
    job = _active_job(edition, output_stage=output_stage)
    pending = read_pending_return(edition, job=job)
    if pending is None or pending.validation_status != WARNING:
        raise DriveReturnError("No WARNING_REQUIRES_CONFIRMATION return is pending")
    report_path = storage.storage_root() / job.validation_report_path
    if report_path.is_symlink() or not report_path.is_file():
        raise DriveReturnError("Validation report is unavailable")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DriveReturnError("Validation report is invalid") from exc
    if (
        report.get("status") != WARNING
        or report.get("job_id") != str(job.job_id)
        or report.get("return_sha256") != pending.sha256
    ):
        raise DriveReturnError("Validation report does not match the pending return")
    confirmed_at = timezone.now()
    report["editorial_confirmation"] = {
        "actor": actor,
        "note": note,
        "confirmed_at": confirmed_at.isoformat(),
    }
    intake_storage.atomic_write_json(report_path, report, overwrite=True)
    type(job).objects.filter(pk=job.pk).update(
        warning_confirmed_at=confirmed_at,
        warning_confirmed_by=actor,
        warning_confirmation_note=note,
        updated_at=confirmed_at,
    )
    job.refresh_from_db()
    return job


def import_translated_return(edition, *, client=None) -> Path:
    result = import_drive_return(edition, client=client, output_stage="translated")
    if result.validation_status == WARNING:
        raise DriveReturnError(
            "WARNING_REQUIRES_CONFIRMATION: confirme editorialmente antes de importar"
        )
    payload, pending, job = validated_pending_payload(edition, output_stage="translated")
    destination = storage.translated_dir(edition.work.code, job.target_language) / job.expected_return_filename
    intake_storage.atomic_write_bytes(destination, payload, overwrite=True)
    EditionPipeline = apps.get_model("editorial", "EditionPipeline")
    with transaction.atomic():
        locked_job = type(job).objects.select_for_update().get(pk=job.pk)
        pipeline_state, _ = EditionPipeline.objects.select_for_update().get_or_create(edition=edition)
        pipeline_state.current_stage = "TRANSLATED"
        pipeline_state.translated_at = timezone.now()
        pipeline_state.translation_language = internal_language(job.target_language)
        pipeline_state.last_log = (
            f"{timezone.now().isoformat()} :: DRIVE_TRANSLATED_IMPORTED :: job_id={job.job_id}"
        )
        pipeline_state.save(update_fields=["current_stage", "translated_at", "translation_language", "last_log"])
        locked_job.status = locked_job.STATUS_COMPLETED
        locked_job.save(update_fields=["status", "updated_at"])
    pending.pending_path.unlink(missing_ok=True)
    pending_metadata_path(edition, job).unlink(missing_ok=True)
    return destination


def save_pending_as_official(edition, *, actor: str = "system") -> OfficialBodyResult:
    payload, pending, job = validated_pending_payload(edition, output_stage="official")
    if job.output_stage != job.STAGE_OFFICIAL:
        raise DriveReturnError("A translated intermediate return cannot become official directly")
    result = official_body.promote(
        edition,
        payload,
        provenance="drive_official",
        source_stage="official",
        translation_job=job,
        input_sha256=job.input_sha256,
        actor=actor,
    )
    pending.pending_path.unlink(missing_ok=True)
    pending_metadata_path(edition, job).unlink(missing_ok=True)
    return result


def import_and_promote_drive_return(edition, *, client=None, actor: str = "system") -> OfficialBodyResult:
    import_drive_return(edition, client=client, output_stage="official")
    return save_pending_as_official(edition, actor=actor)
