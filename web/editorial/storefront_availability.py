from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse

from django.db import models


NOT_ATTACHED = "NOT_ATTACHED"
COMING_SOON = "COMING_SOON"
LAUNCHED = "LAUNCHED"

AVAILABILITY_LABELS = {
    NOT_ATTACHED: "E-book não anexado",
    COMING_SOON: "Lançamento em breve",
    LAUNCHED: "Lançado",
}


@dataclass(frozen=True)
class StorefrontAvailability:
    status: str
    label: str
    ebook_attached: bool
    active_sales_channels: tuple[dict[str, Any], ...]


def install_sales_channels_field() -> None:
    """Add the additive JSON field to EditionMetadata at app startup.

    The schema is created by migration 0025. Keeping the field installer isolated
    avoids changing the large legacy models module while the RinoBooks contract
    remains on its review branch.
    """

    from editorial.models import EditionMetadata

    try:
        EditionMetadata._meta.get_field("sales_channels")
        return
    except models.FieldDoesNotExist:
        pass

    field = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Lojas / canais de venda",
        help_text=(
            "Lista de lojas adicionais. Cada item deve ter name, url e active. "
            "Ex.: {\"name\":\"IngramSpark\",\"url\":\"https://...\",\"active\":true}."
        ),
    )
    field.contribute_to_class(EditionMetadata, "sales_channels")


def _https_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    return url


def _channel(name: Any, url: Any, active: Any = True) -> dict[str, Any] | None:
    normalized_url = _https_url(url)
    normalized_name = str(name or "").strip()
    if not normalized_name or not normalized_url:
        return None
    return {
        "name": normalized_name,
        "url": normalized_url,
        "active": bool(active),
    }


def normalize_sales_channels(metadata) -> list[dict[str, Any]]:
    """Return deduplicated channels, preserving legacy Lulu/Hotmart fields."""

    channels: list[dict[str, Any]] = []

    legacy = (
        ("Lulu", getattr(metadata, "lulu_url", "")),
        ("Hotmart", getattr(metadata, "hotmart_url", "")),
    )
    for name, url in legacy:
        item = _channel(name, url, True)
        if item:
            channels.append(item)

    raw_channels: Iterable[Any] = getattr(metadata, "sales_channels", None) or []
    if isinstance(raw_channels, dict):
        raw_channels = [raw_channels]
    if isinstance(raw_channels, (str, bytes)):
        raw_channels = []

    for raw in raw_channels:
        if not isinstance(raw, dict):
            continue
        item = _channel(
            raw.get("name") or raw.get("store") or raw.get("channel"),
            raw.get("url"),
            raw.get("active", True),
        )
        if item:
            channels.append(item)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in channels:
        key = item["url"].casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def derive_storefront_availability(
    metadata,
    *,
    ebook_attached: bool,
) -> StorefrontAvailability:
    channels = normalize_sales_channels(metadata)
    active = tuple(channel for channel in channels if channel["active"])

    if not ebook_attached:
        status = NOT_ATTACHED
    elif active:
        status = LAUNCHED
    else:
        status = COMING_SOON

    return StorefrontAvailability(
        status=status,
        label=AVAILABILITY_LABELS[status],
        ebook_attached=ebook_attached,
        active_sales_channels=active,
    )
