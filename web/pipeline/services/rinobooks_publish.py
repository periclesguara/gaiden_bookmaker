from __future__ import annotations

import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist

from editorial import kdp_mode
from editorial.services.metadata import MetadataValidation, validate_metadata
from pipeline.services import book_manifest


class RinoBooksPublishError(RuntimeError):
    """Raised when an edition cannot be exported or delivered safely."""


@dataclass(frozen=True)
class PublicationPackage:
    manifest: dict[str, Any]
    manifest_path: Path
    cover_path: Path
    epub_path: Path
    validation: MetadataValidation


@dataclass(frozen=True)
class RinoBooksDraft:
    edition_id: int
    status: str
    duplicate: bool
    replaced_draft: bool


def _required_setting(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RinoBooksPublishError(f"{name} is not configured")
    return value


def _publish_endpoint() -> str:
    base_url = _required_setting("RINOBOOKS_PUBLISH_URL").rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RinoBooksPublishError("RINOBOOKS_PUBLISH_URL must be an HTTPS URL")
    if parsed.path.rstrip("/").endswith("/api/gaiden/editions"):
        return base_url
    return f"{base_url}/api/gaiden/editions"


def _project_root() -> Path:
    return Path(settings.BASE_DIR).resolve().parent


def resolve_cover_path(edition) -> Path:
    configured = (getattr(edition, "cover_filepath", "") or "").strip()
    if configured:
        candidate = Path(configured)
        if not candidate.is_absolute():
            candidate = _project_root() / candidate
        candidate = candidate.resolve()
        if candidate.is_file():
            return candidate

    fallback_dir = (
        _project_root()
        / "data"
        / "covers"
        / edition.work.code
        / edition.language.code
    )
    for filename in ("cover.jpg", "cover.jpeg", "cover.png", "cover.webp"):
        candidate = fallback_dir / filename
        if candidate.is_file():
            return candidate.resolve()

    raise RinoBooksPublishError(
        f"Cover not found for {edition.work.code} [{edition.language.code}]"
    )


def prepare_publication_package(
    edition,
    *,
    export_user: str = "system",
) -> PublicationPackage:
    try:
        metadata = edition.metadata
    except ObjectDoesNotExist:
        metadata = None

    validation = validate_metadata(metadata)
    if not validation.is_valid:
        raise RinoBooksPublishError(
            "Metadata validation failed: " + " | ".join(validation.errors)
        )

    try:
        epub_path = Path(kdp_mode.run_epubcheck_for_edition(edition)).resolve()
    except (OSError, RuntimeError) as exc:
        raise RinoBooksPublishError(f"EPUB validation failed: {exc}") from exc
    if not epub_path.is_file():
        raise RinoBooksPublishError(f"Validated EPUB not found: {epub_path}")

    cover_path = resolve_cover_path(edition)
    manifest_object = book_manifest.build_manifest(
        edition,
        edition,
        export_user=export_user,
        epubcheck_status="pass",
        epub_path_override=epub_path,
    )
    manifest = manifest_object.to_dict()
    if manifest.get("status") != "DRAFT":
        raise RinoBooksPublishError("Manifest publication status must be DRAFT")
    manifest_path = book_manifest.write_manifest(edition, manifest_object).resolve()
    return PublicationPackage(
        manifest=manifest,
        manifest_path=manifest_path,
        cover_path=cover_path,
        epub_path=epub_path,
        validation=validation,
    )


def publish_edition(
    edition,
    *,
    export_user: str = "system",
    session: requests.Session | None = None,
) -> RinoBooksDraft:
    """Send an explicitly validated package and accept only a remote draft."""

    package = prepare_publication_package(edition, export_user=export_user)
    token = _required_setting("RINOBOOKS_PUBLISH_TOKEN")
    client = session or requests.Session()
    cover_type = (
        mimetypes.guess_type(package.cover_path.name)[0]
        or "application/octet-stream"
    )

    try:
        with (
            package.cover_path.open("rb") as cover_file,
            package.epub_path.open("rb") as epub_file,
        ):
            response = client.post(
                _publish_endpoint(),
                headers={"Authorization": f"Bearer {token}"},
                data={"manifest": json.dumps(package.manifest, ensure_ascii=False)},
                files={
                    "cover": (package.cover_path.name, cover_file, cover_type),
                    "epub": (
                        package.epub_path.name,
                        epub_file,
                        "application/epub+zip",
                    ),
                },
                timeout=(10, 180),
            )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, OSError, ValueError) as exc:
        raise RinoBooksPublishError(f"RinoBooks delivery failed: {exc}") from exc

    if not isinstance(payload, dict):
        raise RinoBooksPublishError("RinoBooks returned an invalid draft response")
    draft_id = payload.get("edition_id")
    status = payload.get("status")
    if not isinstance(draft_id, int) or status != "DRAFT":
        raise RinoBooksPublishError("RinoBooks returned an invalid draft response")

    return RinoBooksDraft(
        edition_id=draft_id,
        status=status,
        duplicate=bool(payload.get("duplicate")),
        replaced_draft=bool(payload.get("replaced_draft")),
    )
