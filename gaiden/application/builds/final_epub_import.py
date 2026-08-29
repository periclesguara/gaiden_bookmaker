from __future__ import annotations

import os
import posixpath
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

from django.db import transaction
from django.utils import timezone

from editorial.models import Edition, EditionBuild, EditionBuildAuditEvent, EditionPipeline
from gaiden.application.builds.epubcheck_service import (
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_PASSED_WITH_WARNINGS,
    STATUS_UNAVAILABLE,
    installed_epubcheck_version,
    stream_sha256,
    validate_epubcheck,
    write_report,
)
from gaiden.infrastructure import storage


class FinalEpubImportError(ValueError):
    pass


class EpubValidationError(FinalEpubImportError):
    def __init__(self, message: str, report: dict | None = None):
        super().__init__(message)
        self.report = report or {}


@dataclass(frozen=True)
class FinalEpubImportResult:
    outcome: str
    build: EditionBuild
    destination: Path


def _normalize_locale(value: str) -> str:
    parts = value.strip().replace("_", "-").split("-")
    if not parts or not parts[0]:
        raise FinalEpubImportError("Locale is required.")
    return parts[0].lower() + (f"-{parts[1].upper()}" if len(parts) > 1 else "")


def _version_from_filename(filename: str) -> int | None:
    match = re.search(r"(?:^|[_-])v(\d+)(?:[_\.-]|$)", filename, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _validate_internal_links(archive: zipfile.ZipFile) -> None:
    names = set(archive.namelist())
    xml_suffixes = {".xml", ".xhtml", ".html", ".htm", ".opf", ".ncx", ".svg"}
    for name in sorted(names):
        if PurePosixPath(name).suffix.lower() not in xml_suffixes:
            continue
        try:
            root = ElementTree.fromstring(archive.read(name))
        except ElementTree.ParseError as exc:
            raise EpubValidationError(f"Invalid XML/XHTML in {name}: {exc}") from exc
        base = PurePosixPath(name).parent
        for element in root.iter():
            for key in ("href", "src", "{http://www.w3.org/1999/xlink}href"):
                raw = element.attrib.get(key, "").strip()
                parsed = urlsplit(raw)
                if not raw or parsed.scheme or raw.startswith(("#", "//")):
                    continue
                target = unquote(parsed.path)
                if not target:
                    continue
                normalized = posixpath.normpath(str(PurePosixPath(base, target)))
                if normalized not in names:
                    raise EpubValidationError(f"Broken internal link in {name}: {raw}")


def validate_epub(
    path: Path,
    *,
    skip_epubcheck_for_tests: bool = False,
    require_pass: bool = True,
) -> dict:
    if not zipfile.is_zipfile(path):
        raise EpubValidationError("The supplied artifact is not a valid ZIP/EPUB.")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if not infos or infos[0].filename != "mimetype":
            raise EpubValidationError("EPUB mimetype must be the first archive entry.")
        if infos[0].compress_type != zipfile.ZIP_STORED:
            raise EpubValidationError("EPUB mimetype must be stored without compression.")
        if archive.read("mimetype") != b"application/epub+zip":
            raise EpubValidationError("Invalid EPUB mimetype value.")
        corrupt = archive.testzip()
        if corrupt:
            raise EpubValidationError(f"Corrupt ZIP entry: {corrupt}")
        _validate_internal_links(archive)

    report = validate_epubcheck(path, skip_for_tests=skip_epubcheck_for_tests)
    if require_pass and not report["passed"]:
        message = "EPUBCheck is unavailable in the current environment."
        if report["status"] != STATUS_UNAVAILABLE:
            message = "EPUB validation failed: FATAL or ERROR detected."
        raise EpubValidationError(message, report)
    return report


def _resolve_official_body(edition: Edition, explicit_path: str | Path | None) -> tuple[Path, str]:
    candidates: list[str | Path] = []
    if explicit_path:
        candidates.append(explicit_path)
    state = EditionPipeline.objects.filter(edition=edition).first()
    if state and state.core_last_txt_path:
        candidates.append(state.core_last_txt_path)
    try:
        candidates.extend([edition.texts.normalized_path, edition.texts.raw_path])
    except Exception:
        pass
    candidates.append(edition.raw_source_path)
    allowed_roots = (storage.storage_root().resolve(), storage.repo_root().resolve())
    for value in candidates:
        if not value:
            continue
        candidate = storage.resolve_repo_path(value).resolve()
        if candidate.is_file() and any(candidate.is_relative_to(root) for root in allowed_roots):
            return candidate, stream_sha256(candidate)
    raise FinalEpubImportError("No existing official body in canonical storage is associated with this edition.")


def _epubcheck_fields(report: dict) -> dict[str, object]:
    return {
        "epubcheck_status": report.get("status", STATUS_FAILED),
        "epubcheck_version": report.get("tool_version", ""),
        "epubcheck_run_at": timezone.now(),
        "epubcheck_returncode": report.get("returncode"),
        "epubcheck_fatal_count": report.get("fatal_count", 0),
        "epubcheck_error_count": report.get("error_count", 0),
        "epubcheck_warning_count": report.get("warning_count", 0),
        "epubcheck_validated_sha256": report.get("epub_sha256", ""),
        "epubcheck_report_path": report.get("report_path", ""),
        "epubcheck_report_sha256": report.get("report_sha256", ""),
    }


def _record_failed_validation(build_id: int, *, actor: str, error: Exception, report: dict) -> None:
    with transaction.atomic():
        build = EditionBuild.objects.select_for_update().get(pk=build_id)
        build.status = EditionBuild.STATUS_FAILED
        build.validation_passed = False
        build.validation_report = report
        build.notes = str(error)
        build.is_final = False
        for field, value in _epubcheck_fields(report).items():
            setattr(build, field, value)
        build.save(update_fields=[
            "status", "is_final", "validation_passed", "validation_report", "notes",
            *list(_epubcheck_fields(report)),
        ])
        EditionBuildAuditEvent.objects.create(
            build=build,
            event_type="FINAL_ARTIFACT_VALIDATION_FAILED",
            actor=actor,
            details={"error": str(error), "validation": report},
        )


def import_final_epub(
    *,
    edition_id: int,
    locale: str,
    source_path: str | Path,
    expected_sha256: str,
    expected_size_bytes: int,
    source: str = "EXTERNAL_FINAL_UPLOAD",
    approved: bool = False,
    actor: str = "system",
    official_body_path: str | Path | None = None,
    skip_epubcheck_for_tests: bool = False,
) -> FinalEpubImportResult:
    if not approved:
        raise FinalEpubImportError("Explicit final-artifact approval is required.")
    requested_locale = _normalize_locale(locale)
    source_file = Path(source_path).expanduser().resolve()
    if not source_file.is_file() or source_file.suffix.casefold() != ".epub":
        raise FinalEpubImportError("The final .epub artifact does not exist.")
    actual_size = source_file.stat().st_size
    actual_sha = stream_sha256(source_file)
    if actual_size != expected_size_bytes:
        raise FinalEpubImportError(f"Size mismatch: expected {expected_size_bytes}, got {actual_size}.")
    if actual_sha.casefold() != expected_sha256.strip().casefold():
        raise FinalEpubImportError(f"SHA-256 mismatch: expected {expected_sha256}, got {actual_sha}.")

    with transaction.atomic():
        try:
            edition = Edition.objects.select_for_update().select_related("work", "language", "seal").get(pk=edition_id)
        except Edition.DoesNotExist as exc:
            raise FinalEpubImportError(f"Edition {edition_id} does not exist.") from exc
        edition_base = _normalize_locale(edition.language.code).split("-", 1)[0]
        if requested_locale.split("-", 1)[0] != edition_base:
            raise FinalEpubImportError(
                f"Locale mismatch: edition is {edition.language.code}, requested {requested_locale}."
            )
        body_path, body_sha = _resolve_official_body(edition, official_body_path)
        declared_version = _version_from_filename(source_file.name)
        latest_version = (
            EditionBuild.objects.filter(edition=edition)
            .order_by("-build_version")
            .values_list("build_version", flat=True)
            .first()
            or 0
        )
        build_version = declared_version or (latest_version + 1)
        occupied = EditionBuild.objects.select_for_update().filter(
            edition=edition, language_code=requested_locale, build_version=build_version
        ).first()
        if occupied:
            exact = (
                occupied.locale == requested_locale
                and occupied.artifact_sha256 == actual_sha
                and occupied.artifact_size_bytes == actual_size
                and occupied.artifact_source == source
            )
            version_matches = (
                skip_epubcheck_for_tests
                or occupied.epubcheck_version == installed_epubcheck_version()
            )
            if exact and occupied.qualifies_as_done and version_matches:
                return FinalEpubImportResult("NO_OP", occupied, Path(occupied.epub_path))
            if not exact:
                raise FinalEpubImportError(
                    f"V{build_version} for {requested_locale} is already occupied by different bytes or metadata."
                )
            build = occupied
            build.status = EditionBuild.STATUS_VALIDATING
            build.validation_passed = False
            build.save(update_fields=["status", "validation_passed"])
        else:
            build = EditionBuild.objects.create(
                edition=edition,
                language_code=requested_locale,
                locale=requested_locale,
                build_version=build_version,
                build_type=(
                    EditionBuild.BUILD_TYPE_INITIAL if latest_version == 0 else EditionBuild.BUILD_TYPE_REBUILD
                ),
                status=EditionBuild.STATUS_VALIDATING,
                artifact_sha256=actual_sha,
                artifact_size_bytes=actual_size,
                artifact_source=source,
                official_body_path=str(body_path),
                official_body_sha256=body_sha,
            )
        build_id = build.id

    destination_dir = storage.builds_dir(edition.work.code, requested_locale).resolve()
    canonical_root = storage.storage_root().resolve()
    if not destination_dir.is_relative_to(canonical_root):
        _record_failed_validation(
            build_id, actor=actor, error=FinalEpubImportError("Destination escapes canonical storage."), report={}
        )
        raise FinalEpubImportError("Destination escapes canonical storage.")
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = (destination_dir / source_file.name).resolve()
    descriptor, temporary_name = tempfile.mkstemp(prefix=".final-", suffix=".epub", dir=destination_dir)
    os.close(descriptor)
    temporary = Path(temporary_name)
    destination_created = False
    EditionBuild.objects.filter(pk=build_id).update(
        epubcheck_status=EditionBuild.EPUBCHECK_RUNNING,
        epubcheck_run_at=timezone.now(),
    )
    try:
        shutil.copyfile(source_file, temporary)
        if temporary.stat().st_size != actual_size or stream_sha256(temporary) != actual_sha:
            raise EpubValidationError("Temporary artifact failed size/SHA-256 verification.")
        try:
            with transaction.atomic():
                build = EditionBuild.objects.select_for_update().select_related("edition").get(pk=build_id)
                if destination.exists():
                    if destination.stat().st_size != actual_size or stream_sha256(destination) != actual_sha:
                        raise FinalEpubImportError(
                            "Destination filename already exists with different bytes; refusing overwrite."
                        )
                else:
                    os.replace(temporary, destination)
                    destination_created = True
                try:
                    report = validate_epub(
                        destination,
                        skip_epubcheck_for_tests=skip_epubcheck_for_tests,
                        require_pass=False,
                    )
                except EpubValidationError as exc:
                    report = {
                        "schema": "gaiden_epubcheck_report_v1",
                        "tool": "EPUBCheck",
                        "tool_version": "not-run",
                        "status": STATUS_FAILED,
                        "passed": False,
                        "epub_path": str(destination),
                        "epub_sha256": actual_sha,
                        "returncode": None,
                        "fatal_count": 0,
                        "error_count": 1,
                        "warning_count": 0,
                        "stdout": "",
                        "stderr": str(exc),
                        "internal_qa_failed": True,
                        "started_at": timezone.now().isoformat(),
                        "completed_at": timezone.now().isoformat(),
                        "duration_seconds": 0,
                    }
                report_path, report_sha = write_report(destination.with_suffix(".epubcheck.json"), report)
                report["report_path"] = str(report_path)
                report["report_sha256"] = report_sha
                if not report["passed"]:
                    message = "EPUBCheck is unavailable in the current environment."
                    if report["status"] != STATUS_UNAVAILABLE:
                        message = "EPUB validation failed: FATAL or ERROR detected."
                    raise EpubValidationError(message, report)
                now = timezone.now()
                previous = list(
                    EditionBuild.objects.select_for_update().filter(
                        edition=build.edition, status=EditionBuild.STATUS_DONE, is_final=True
                    ).exclude(pk=build.pk)
                )
                for old_build in previous:
                    old_build.status = EditionBuild.STATUS_OUTDATED
                    old_build.is_final = False
                    old_build.save(update_fields=["status", "is_final"])
                    EditionBuildAuditEvent.objects.create(
                        build=old_build,
                        event_type="FINAL_ARTIFACT_SUPERSEDED",
                        actor=actor,
                        details={"replacement_sha256": actual_sha, "replacement_build_id": build.id},
                    )
                build.epub_path = str(destination)
                build.status = EditionBuild.STATUS_DONE
                build.is_final = True
                build.validation_passed = True
                build.validation_report = report
                for field, value in _epubcheck_fields(report).items():
                    setattr(build, field, value)
                build.validated_at = now
                build.approved_at = now
                build.completed_at = now
                build.notes = "Imported approved final EPUB; bytes were not rebuilt or modified."
                build.save()
                EditionBuildAuditEvent.objects.create(
                    build=build,
                    event_type="FINAL_ARTIFACT_IMPORTED",
                    actor=actor,
                    details={
                        "source": source,
                        "source_path": str(source_file),
                        "destination": str(destination),
                        "sha256": actual_sha,
                        "size_bytes": actual_size,
                        "locale": requested_locale,
                        "official_body_sha256": body_sha,
                        "validation": report,
                    },
                )
                state, _ = EditionPipeline.objects.select_for_update().get_or_create(edition=build.edition)
                state.current_stage = "DONE"
                state.build_outdated = False
                state.editorial_changed = False
                state.last_built_at = now
                state.last_version_path = str(destination)
                state.last_version_filename = destination.name
                state.save(update_fields=[
                    "current_stage", "build_outdated", "editorial_changed", "last_built_at",
                    "last_version_path", "last_version_filename",
                ])
                from gaiden.application.builds.finalized_projects import sync_finalized_project

                transaction.on_commit(
                    lambda: sync_finalized_project(build.edition_id, actor=actor),
                    robust=True,
                )
        except Exception:
            if destination_created:
                destination.unlink(missing_ok=True)
            raise
    except Exception as exc:
        report = getattr(exc, "report", {})
        _record_failed_validation(build_id, actor=actor, error=exc, report=report)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return FinalEpubImportResult("IMPORTED", build, destination)


def revalidate_registered_final_build(build: EditionBuild, *, actor: str) -> dict:
    path = EditionBuild._safe_existing_path(build.epub_path, require_epub=True)
    if path is None:
        raise FinalEpubImportError("Registered final EPUB is missing or outside canonical storage.")
    report = validate_epub(path, require_pass=False)
    report_path, report_sha = write_report(path.with_suffix(".epubcheck.json"), report)
    report["report_path"] = str(report_path)
    report["report_sha256"] = report_sha
    with transaction.atomic():
        locked = EditionBuild.objects.select_for_update().get(pk=build.pk)
        locked.validation_report = report
        locked.validation_passed = bool(report["passed"])
        if not report["passed"]:
            locked.status = EditionBuild.STATUS_FAILED
            locked.is_final = False
        for field, value in _epubcheck_fields(report).items():
            setattr(locked, field, value)
        locked.validated_at = timezone.now()
        locked.save(update_fields=[
            "status", "is_final", "validation_report", "validation_passed", "validated_at", *list(_epubcheck_fields(report)),
        ])
        EditionBuildAuditEvent.objects.create(
            build=locked,
            event_type="FINAL_ARTIFACT_REVALIDATED",
            actor=actor,
            details={"validation": report, "artifact_sha256": locked.artifact_sha256},
        )
    return report


def invalidate_epubcheck_for_changed_artifact(
    build: EditionBuild,
    *,
    actor: str,
    reason: str,
) -> bool:
    """Fail closed when the bytes available for download differ from the pass."""
    if build.epubcheck_status not in {STATUS_PASSED, STATUS_PASSED_WITH_WARNINGS}:
        return False
    with transaction.atomic():
        locked = EditionBuild.objects.select_for_update().select_related("edition").get(pk=build.pk)
        if locked.epubcheck_status not in {STATUS_PASSED, STATUS_PASSED_WITH_WARNINGS}:
            return False
        locked.epubcheck_status = "EPUBCHECK_PENDING"
        locked.validation_passed = False
        locked.is_final = False
        locked.status = EditionBuild.STATUS_READY_FOR_APPROVAL
        locked.notes = f"EPUBCheck invalidated: {reason}"
        locked.save(update_fields=[
            "epubcheck_status", "validation_passed", "is_final", "status", "notes",
        ])
        EditionBuildAuditEvent.objects.create(
            build=locked,
            event_type="EPUBCHECK_INVALIDATED",
            actor=actor,
            details={
                "reason": reason,
                "validated_sha256": locked.epubcheck_validated_sha256,
                "artifact_sha256": locked.artifact_sha256,
            },
        )
        state, _ = EditionPipeline.objects.select_for_update().get_or_create(edition=locked.edition)
        state.current_stage = "FINAL_MD"
        state.build_outdated = True
        state.editorial_changed = True
        state.last_log = "EPUBCheck invalidated; rebuild and revalidate the final EPUB."
        state.save(update_fields=["current_stage", "build_outdated", "editorial_changed", "last_log"])
    return True
