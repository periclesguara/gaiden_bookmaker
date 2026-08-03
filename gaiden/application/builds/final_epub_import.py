from __future__ import annotations

import hashlib
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

from django.db import transaction
from django.utils import timezone

from editorial.models import Edition, EditionBuild, EditionBuildAuditEvent, EditionPipeline
from gaiden.infrastructure import storage


class FinalEpubImportError(ValueError):
    pass


@dataclass(frozen=True)
class FinalEpubImportResult:
    outcome: str
    build: EditionBuild
    destination: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
            raise FinalEpubImportError(f"Invalid XML/XHTML in {name}: {exc}") from exc
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
                    raise FinalEpubImportError(f"Broken internal link in {name}: {raw}")


def validate_epub(path: Path, *, run_epubcheck: bool = True) -> None:
    if not zipfile.is_zipfile(path):
        raise FinalEpubImportError("The supplied artifact is not a valid ZIP/EPUB.")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if not infos or infos[0].filename != "mimetype":
            raise FinalEpubImportError("EPUB mimetype must be the first archive entry.")
        if infos[0].compress_type != zipfile.ZIP_STORED:
            raise FinalEpubImportError("EPUB mimetype must be stored without compression.")
        if archive.read("mimetype") != b"application/epub+zip":
            raise FinalEpubImportError("Invalid EPUB mimetype value.")
        corrupt = archive.testzip()
        if corrupt:
            raise FinalEpubImportError(f"Corrupt ZIP entry: {corrupt}")
        _validate_internal_links(archive)
    if run_epubcheck and shutil.which("epubcheck"):
        result = subprocess.run(
            ["epubcheck", str(path)], capture_output=True, text=True, check=False, timeout=180
        )
        if result.returncode:
            summary = (result.stdout + "\n" + result.stderr).strip()[-4000:]
            raise FinalEpubImportError(f"EPUBCheck failed:\n{summary}")


def _resolve_official_body(edition: Edition, explicit_path: str | Path | None) -> tuple[Path, str]:
    candidates: list[str | Path] = []
    if explicit_path:
        candidates.append(explicit_path)
    state = EditionPipeline.objects.filter(edition=edition).first()
    if state and state.core_last_txt_path:
        candidates.append(state.core_last_txt_path)
    try:
        texts = edition.texts
        candidates.extend([texts.normalized_path, texts.raw_path])
    except Exception:
        pass
    candidates.append(edition.raw_source_path)
    for value in candidates:
        if not value:
            continue
        candidate = storage.resolve_repo_path(value).resolve()
        if candidate.is_file():
            return candidate, _sha256(candidate)
    raise FinalEpubImportError("No existing official body is associated with this edition.")


@transaction.atomic
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
    run_epubcheck: bool = True,
) -> FinalEpubImportResult:
    if not approved:
        raise FinalEpubImportError("Explicit final-artifact approval is required.")
    requested_locale = _normalize_locale(locale)
    source_file = Path(source_path).expanduser().resolve()
    if not source_file.is_file():
        raise FinalEpubImportError(f"EPUB does not exist: {source_file}")
    if source_file.name.lower().endswith(".epub") is False:
        raise FinalEpubImportError("Final artifact must be an .epub file.")
    actual_size = source_file.stat().st_size
    actual_sha = _sha256(source_file)
    if actual_size != expected_size_bytes:
        raise FinalEpubImportError(f"Size mismatch: expected {expected_size_bytes}, got {actual_size}.")
    if actual_sha.lower() != expected_sha256.strip().lower():
        raise FinalEpubImportError(f"SHA-256 mismatch: expected {expected_sha256}, got {actual_sha}.")
    validate_epub(source_file, run_epubcheck=run_epubcheck)

    try:
        edition = Edition.objects.select_for_update().select_related("work", "language", "seal").get(pk=edition_id)
    except Edition.DoesNotExist as exc:
        raise FinalEpubImportError(f"Edition {edition_id} does not exist.") from exc
    edition_base_locale = _normalize_locale(edition.language.code).split("-", 1)[0]
    if requested_locale.split("-", 1)[0] != edition_base_locale:
        raise FinalEpubImportError(
            f"Locale mismatch: edition is {edition.language.code}, requested {requested_locale}."
        )
    existing = EditionBuild.objects.filter(edition=edition, artifact_sha256=actual_sha).first()
    if existing:
        return FinalEpubImportResult("NO_OP", existing, Path(existing.epub_path))

    body_path, body_sha = _resolve_official_body(edition, official_body_path)
    destination_dir = storage.builds_dir(edition.work.code, requested_locale)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = (destination_dir / source_file.name).resolve()
    if destination.exists():
        if _sha256(destination) != actual_sha:
            raise FinalEpubImportError("Destination filename already exists with different bytes; refusing overwrite.")
    else:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".final-", suffix=".epub", dir=destination_dir)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source_file, temporary)
            if _sha256(temporary) != actual_sha:
                raise FinalEpubImportError("Destination hash verification failed.")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    now = timezone.now()
    previous = list(
        EditionBuild.objects.select_for_update().filter(
            edition=edition, status=EditionBuild.STATUS_DONE, is_final=True
        )
    )
    for old_build in previous:
        old_build.status = EditionBuild.STATUS_OUTDATED
        old_build.is_final = False
        old_build.save(update_fields=["status", "is_final"])
        EditionBuildAuditEvent.objects.create(
            build=old_build,
            event_type="FINAL_ARTIFACT_SUPERSEDED",
            actor=actor,
            details={"replacement_sha256": actual_sha},
        )
    latest_version = (
        EditionBuild.objects.filter(edition=edition)
        .order_by("-build_version")
        .values_list("build_version", flat=True)
        .first()
        or 0
    )
    requested_version = _version_from_filename(source_file.name)
    build_version = max(latest_version + 1, requested_version or 0)
    if EditionBuild.objects.filter(
        edition=edition,
        language_code=requested_locale,
        build_version=build_version,
    ).exists():
        raise FinalEpubImportError(f"Build version V{build_version} already exists for {requested_locale}.")
    build = EditionBuild.objects.create(
        edition=edition,
        language_code=requested_locale,
        locale=requested_locale,
        build_version=build_version,
        build_type=EditionBuild.BUILD_TYPE_INITIAL if latest_version == 0 else EditionBuild.BUILD_TYPE_REBUILD,
        epub_path=str(destination),
        status=EditionBuild.STATUS_DONE,
        artifact_sha256=actual_sha,
        artifact_size_bytes=actual_size,
        artifact_source=source,
        is_final=True,
        validation_passed=True,
        official_body_path=str(body_path),
        official_body_sha256=body_sha,
        validated_at=now,
        approved_at=now,
        completed_at=now,
        notes="Imported approved final EPUB; bytes were not rebuilt or modified.",
    )
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
        },
    )
    state, _ = EditionPipeline.objects.select_for_update().get_or_create(edition=edition)
    state.current_stage = "DONE"
    state.build_outdated = False
    state.last_built_at = now
    state.last_version_path = str(destination)
    state.last_version_filename = destination.name
    state.save(update_fields=[
        "current_stage", "build_outdated", "last_built_at", "last_version_path", "last_version_filename"
    ])
    return FinalEpubImportResult("IMPORTED", build, destination)
