from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from django.apps import apps
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from gaiden.infrastructure import intake_storage, storage
from gaiden.infrastructure.intake_drive import DriveFile, RcloneClient


TRANSLATION_JOBS_ROOT = "04_TRANSLATION_JOBS"


class DriveReturnError(RuntimeError):
    pass


@dataclass(frozen=True)
class DriveReturnLink:
    item_id: int
    folder: str
    book_code: str
    title_slug: str
    target_language: str
    canonical_filename: str
    normalized_sha256: str


@dataclass(frozen=True)
class DriveReturnResult:
    pending_path: Path
    remote_filename: str
    sha256: str
    size: int


@dataclass(frozen=True)
class PendingDriveReturn:
    pending_path: Path
    remote_filename: str
    remote_folder: str
    target_language: str
    sha256: str
    size: int


@dataclass(frozen=True)
class TranslationDriveExportResult:
    source_path: Path
    remote_path: str
    return_folder: str
    return_filename: str
    no_op: bool


@dataclass(frozen=True)
class OfficialBodyResult:
    path: Path
    snapshot_id: int
    sha256: str
    size: int
    no_op: bool


def pending_path(edition) -> Path:
    return storage.editions_dir(edition.id) / "core" / "drive_return_pending.txt"


def pending_metadata_path(edition) -> Path:
    return storage.editions_dir(edition.id) / "core" / "drive_return_pending.json"


def miolo_oficial_path(edition) -> Path:
    return storage.editions_dir(edition.id) / "core" / "miolo_oficial.txt"


def _canonical_language(value: str) -> str:
    language = (value or "").strip().lower().replace("_", "-")
    if not language or "/" in language or "\\" in language or ".." in language:
        raise DriveReturnError("The edition target language is invalid")
    return language


def _title_slug(value: str) -> str:
    normalized = slugify(value or "").replace("-", "_")
    if not normalized:
        raise DriveReturnError("The confirmed title is required for Drive return matching")
    return normalized


def _normalized_source_sha256(edition) -> str:
    EditionText = apps.get_model("editorial", "EditionText")
    texts = EditionText.objects.filter(edition=edition).first()
    configured = (texts.normalized_path if texts else "") or ""
    candidates: list[Path] = []
    if configured:
        configured_path = Path(configured)
        candidates.append(
            configured_path
            if configured_path.is_absolute()
            else storage.repo_root() / configured_path
        )
    source_language = edition.language.code
    if edition.work.original_language_id:
        source_language = edition.work.original_language.code
    candidates.append(storage.normalized_path(edition.work.code, source_language))
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return hashlib.sha256(candidate.read_bytes()).hexdigest()
    raise DriveReturnError("The normalized input is unavailable for SHA validation")


def resolve_drive_return_link(edition, *, require_normalized: bool = True) -> DriveReturnLink:
    IntakeItem = apps.get_model("intake_module", "IntakeItem")
    queryset = (
        IntakeItem.objects.filter(duplicate_of__isnull=True)
        .order_by("id")
    )
    item = queryset.filter(handoff_edition_id=edition.id).first()
    if item is None:
        Edition = apps.get_model("editorial", "Edition")
        work_edition_ids = Edition.objects.filter(work_id=edition.work_id).values_list(
            "id", flat=True
        )
        item = queryset.filter(handoff_edition_id__in=work_edition_ids).first()
    if item is None:
        raise DriveReturnError("This edition is not linked to an Intake item")

    book_code = (item.book_code or "").strip()
    if book_code != edition.work.code:
        raise DriveReturnError("The Intake book code does not match this edition")
    item_target = _canonical_language(item.target_language)
    EditionPipeline = apps.get_model("editorial", "EditionPipeline")
    pipeline_target = (
        EditionPipeline.objects.filter(edition=edition)
        .values_list("translation_language", flat=True)
        .first()
        or ""
    )
    edition_target = _canonical_language(pipeline_target or edition.language.code)
    if item_target != edition_target:
        raise DriveReturnError("The Intake target language does not match this edition")

    title_slug = _title_slug(item.confirmed_title)
    folder = f"{book_code}/{edition_target}/return"
    canonical_filename = (
        f"{book_code}_{title_slug}_clean_translate_{edition_target}.txt"
    )
    return DriveReturnLink(
        item_id=item.id,
        folder=folder,
        book_code=book_code,
        title_slug=title_slug,
        target_language=edition_target,
        canonical_filename=canonical_filename,
        normalized_sha256=(
            _normalized_source_sha256(edition) if require_normalized else ""
        ),
    )


def translation_input_folder(edition) -> str:
    link = resolve_drive_return_link(edition, require_normalized=False)
    return f"{link.book_code}/{link.target_language}/input"


def translation_input_filename(edition) -> str:
    link = resolve_drive_return_link(edition, require_normalized=False)
    return f"{link.book_code}_{link.title_slug}_heading_clean_{link.target_language}.txt"


def export_translation_job(edition, *, client=None) -> TranslationDriveExportResult:
    link = resolve_drive_return_link(edition, require_normalized=False)
    source_path = storage.heading_cleaner_dir(link.book_code) / "clean.txt"
    if source_path.is_symlink() or not source_path.is_file():
        raise DriveReturnError("HeadingCleaner output is not available")
    if not source_path.read_text(encoding="utf-8").strip():
        raise DriveReturnError("HeadingCleaner output is empty")

    folder = translation_input_folder(edition)
    filename = translation_input_filename(edition)
    drive_client = client or RcloneClient(inbox=TRANSLATION_JOBS_ROOT)
    drive_client.check_available()
    result = drive_client.upload_file_to_path(source_path, folder, filename)
    return TranslationDriveExportResult(
        source_path=source_path,
        remote_path=result["remote_path"],
        return_folder=link.folder,
        return_filename=link.canonical_filename,
        no_op=bool(result["no_op"]),
    )


def _select_return_file(link: DriveReturnLink, files: list[DriveFile]) -> DriveFile:
    matches: list[DriveFile] = []
    for drive_file in files:
        filename = Path(drive_file.name).name
        relative = PurePosixPath(drive_file.relative_path)
        if relative.is_absolute() or ".." in relative.parts or relative.parent != PurePosixPath("."):
            continue
        if filename != link.canonical_filename:
            continue
        lowered = filename.casefold()
        required_suffix = f"clean_translate_{link.target_language}.txt".casefold()
        if not filename.startswith(link.book_code):
            continue
        if not lowered.endswith(required_suffix):
            continue
        if "heading_clean" in lowered:
            continue
        matches.append(drive_file)
    if len(matches) != 1:
        raise DriveReturnError("The exact canonical TXT return was not found")
    return matches[0]


def read_pending_return(edition) -> PendingDriveReturn | None:
    text_path = pending_path(edition)
    metadata_path = pending_metadata_path(edition)
    if (
        text_path.is_symlink()
        or metadata_path.is_symlink()
        or not text_path.is_file()
        or not metadata_path.is_file()
    ):
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        payload = text_path.read_bytes()
        sha256 = hashlib.sha256(payload).hexdigest()
        pending = PendingDriveReturn(
            pending_path=text_path,
            remote_filename=str(metadata["remote_filename"]),
            remote_folder=str(metadata["remote_folder"]),
            target_language=str(metadata["target_language"]),
            sha256=str(metadata["sha256"]),
            size=int(metadata["size"]),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if pending.sha256 != sha256 or pending.size != len(payload):
        return None
    return pending


def validated_pending_payload(edition) -> tuple[bytes, PendingDriveReturn]:
    pending = read_pending_return(edition)
    if pending is None:
        raise DriveReturnError("No validated Drive return is awaiting save")
    link = resolve_drive_return_link(edition)
    if (
        pending.remote_filename != link.canonical_filename
        or pending.remote_folder != link.folder
        or pending.target_language != link.target_language
    ):
        raise DriveReturnError("Pending Drive return metadata does not match this edition")
    payload = pending.pending_path.read_bytes()
    sha256 = hashlib.sha256(payload).hexdigest()
    if sha256 == link.normalized_sha256:
        raise DriveReturnError("Drive return is identical to the normalized input")
    return payload, pending


def import_drive_return(edition, *, client=None) -> DriveReturnResult:
    link = resolve_drive_return_link(edition)
    folder_parts = PurePosixPath(link.folder).parts
    if (
        len(folder_parts) != 3
        or folder_parts[0] != link.book_code
        or folder_parts[1] != link.target_language
        or folder_parts[2] != "return"
    ):
        raise DriveReturnError("Drive return path must be inside the canonical return folder")

    drive_client = client or RcloneClient(inbox=TRANSLATION_JOBS_ROOT)
    drive_client.check_available()
    selected = _select_return_file(link, drive_client.list_files(link.folder))

    destination = pending_path(edition)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".drive_return_",
        suffix=".txt",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        drive_client.download_file(link.folder, selected, temporary)
        if temporary.is_symlink() or not temporary.is_file():
            raise DriveReturnError("Drive return did not produce a regular file")
        payload = temporary.read_bytes()
        if not payload:
            raise DriveReturnError("Drive return is empty")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DriveReturnError("Drive return must be UTF-8 text") from exc
        if not text.strip():
            raise DriveReturnError("Drive return contains no text")
        sha256 = hashlib.sha256(payload).hexdigest()
        if sha256 == link.normalized_sha256:
            raise DriveReturnError("Drive return is identical to the normalized input")
    finally:
        temporary.unlink(missing_ok=True)

    metadata_path = pending_metadata_path(edition)
    previous_payload = (
        destination.read_bytes()
        if destination.is_file() and not destination.is_symlink()
        else None
    )
    previous_metadata = (
        metadata_path.read_bytes()
        if metadata_path.is_file() and not metadata_path.is_symlink()
        else None
    )
    try:
        intake_storage.atomic_write_bytes(destination, payload, overwrite=True)
        intake_storage.atomic_write_json(
            metadata_path,
            {
                "schema": "gaiden_drive_return_pending_v1",
                "remote_root": TRANSLATION_JOBS_ROOT,
                "remote_folder": link.folder,
                "remote_filename": selected.name,
                "target_language": link.target_language,
                "sha256": sha256,
                "size": len(payload),
            },
            overwrite=True,
        )
    except Exception:
        if previous_payload is None:
            destination.unlink(missing_ok=True)
        else:
            intake_storage.atomic_write_bytes(
                destination, previous_payload, overwrite=True
            )
        if previous_metadata is None:
            metadata_path.unlink(missing_ok=True)
        else:
            intake_storage.atomic_write_bytes(
                metadata_path, previous_metadata, overwrite=True
            )
        raise

    return DriveReturnResult(
        pending_path=destination,
        remote_filename=selected.name,
        sha256=sha256,
        size=len(payload),
    )


def save_pending_as_official(edition) -> OfficialBodyResult:
    payload, pending = validated_pending_payload(edition)
    content = payload.decode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    canonical = miolo_oficial_path(edition)
    if canonical.exists() and (canonical.is_symlink() or not canonical.is_file()):
        raise DriveReturnError("The official body destination is invalid")

    EditionPipeline = apps.get_model("editorial", "EditionPipeline")
    TextSnapshot = apps.get_model("pipeline", "TextSnapshot")
    active = (
        TextSnapshot.objects.filter(edition=edition, stage="drive_return_reference")
        .order_by("-id")
        .first()
    )
    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
    if (
        canonical.is_file()
        and hashlib.sha256(canonical.read_bytes()).hexdigest() == digest
        and active is not None
        and hashlib.sha256(active.content.encode("utf-8")).hexdigest() == digest
        and Path(pipeline_state.core_last_txt_path or "") == canonical
    ):
        pending.pending_path.unlink(missing_ok=True)
        pending_metadata_path(edition).unlink(missing_ok=True)
        return OfficialBodyResult(
            path=canonical,
            snapshot_id=active.id,
            sha256=digest,
            size=len(payload),
            no_op=True,
        )

    previous_payload = canonical.read_bytes() if canonical.is_file() else None
    try:
        with transaction.atomic():
            pipeline_state, _ = EditionPipeline.objects.select_for_update().get_or_create(
                edition=edition
            )
            intake_storage.atomic_write_text(canonical, content, overwrite=True)
            pipeline_state.core_last_txt_path = str(canonical)
            pipeline_state.last_log = (
                f"{timezone.now().isoformat()} :: DRIVE_RETURN_MIOLO_OFICIAL_SAVED"
            )
            pipeline_state.save(update_fields=["core_last_txt_path", "last_log"])
            TextSnapshot.objects.filter(
                edition=edition,
                stage="drive_return_reference",
            ).update(stage="drive_return_reference_superseded")
            snapshot = TextSnapshot.objects.create(
                edition=edition,
                language=resolve_drive_return_link(edition).target_language,
                stage="drive_return_reference",
                source_path=str(canonical),
                content=content,
            )
    except Exception:
        if previous_payload is None:
            canonical.unlink(missing_ok=True)
        else:
            intake_storage.atomic_write_bytes(canonical, previous_payload, overwrite=True)
        raise

    if hasattr(edition, "_state"):
        edition._state.fields_cache["pipeline"] = pipeline_state

    pending.pending_path.unlink(missing_ok=True)
    pending_metadata_path(edition).unlink(missing_ok=True)
    return OfficialBodyResult(
        path=canonical,
        snapshot_id=snapshot.id,
        sha256=digest,
        size=len(payload),
        no_op=False,
    )


def import_and_promote_drive_return(edition, *, client=None) -> OfficialBodyResult:
    import_drive_return(edition, client=client)
    return save_pending_as_official(edition)
