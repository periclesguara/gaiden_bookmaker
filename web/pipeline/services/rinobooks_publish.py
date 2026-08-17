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
from django.utils.text import slugify

from editorial import kdp_mode
from pipeline.services import book_manifest


class RinoBooksPublishError(RuntimeError):
    """Raised when an edition cannot be delivered safely to RinoBooks."""


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
    return f"{base_url}/api/gaiden/editions"


def _project_root() -> Path:
    return Path(settings.BASE_DIR).resolve().parent


def _cover_path(edition) -> Path:
    configured = (getattr(edition, "cover_filepath", "") or "").strip()
    if configured:
        candidate = Path(configured)
        if not candidate.is_absolute():
            candidate = _project_root() / candidate
        candidate = candidate.resolve()
        if candidate.is_file():
            return candidate

    fallback_dir = _project_root() / "data" / "covers" / edition.work.code / edition.language.code
    for filename in ("cover.jpg", "cover.jpeg", "cover.png", "cover.webp"):
        candidate = fallback_dir / filename
        if candidate.is_file():
            return candidate.resolve()

    raise RinoBooksPublishError(
        f"Cover not found for {edition.work.code} [{edition.language.code}]"
    )


def _storefront_payload(edition) -> dict[str, Any]:
    title = (getattr(edition, "title", "") or "").strip() or edition.work.title
    author = (getattr(edition, "author", "") or "").strip() or edition.work.author.name
    language = edition.language.code
    slug = slugify(f"{title}-{language}")

    return {
        "slug": slug,
        "title": title,
        "subtitle": (getattr(edition, "subtitle", "") or "").strip(),
        "author": author,
        "description": (getattr(edition, "about_edition_text", "") or "").strip(),
        "isbn": (getattr(edition, "isbn", "") or "").strip(),
        "rights_statement": (getattr(edition, "copyright_text", "") or "").strip(),
        "price_cents": getattr(edition, "price_cents", None),
        "currency": (getattr(edition, "currency", "") or "BRL").strip().upper(),
    }


def publish_edition(edition, *, session: requests.Session | None = None) -> RinoBooksDraft:
    """Run EPUBCheck and send one immutable edition package as a RinoBooks draft."""

    try:
        epub_path = Path(kdp_mode.run_epubcheck_for_edition(edition)).resolve()
    except (OSError, RuntimeError) as exc:
        raise RinoBooksPublishError(f"EPUB validation failed: {exc}") from exc
    if not epub_path.is_file():
        raise RinoBooksPublishError(f"Validated EPUB not found: {epub_path}")

    cover_path = _cover_path(edition)
    manifest = book_manifest.build_manifest(
        edition,
        edition,
        export_user="rinobooks-publisher",
        epubcheck_status="pass",
    ).to_dict()
    manifest["storefront"] = _storefront_payload(edition)

    token = _required_setting("RINOBOOKS_PUBLISH_TOKEN")
    client = session or requests.Session()
    cover_type = mimetypes.guess_type(cover_path.name)[0] or "application/octet-stream"

    try:
        with cover_path.open("rb") as cover_file, epub_path.open("rb") as epub_file:
            response = client.post(
                _publish_endpoint(),
                headers={"Authorization": f"Bearer {token}"},
                data={"manifest": json.dumps(manifest, ensure_ascii=False)},
                files={
                    "cover": (cover_path.name, cover_file, cover_type),
                    "epub": (epub_path.name, epub_file, "application/epub+zip"),
                },
                timeout=(10, 180),
            )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, OSError, ValueError) as exc:
        raise RinoBooksPublishError(f"RinoBooks delivery failed: {exc}") from exc

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
