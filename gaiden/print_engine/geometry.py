"""Print geometry derived from an immutable :class:`PrintSpec`."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from .specs import Binding, PrintSpec


@dataclass(frozen=True)
class CoverGeometry:
    trim_width_in: Decimal
    trim_height_in: Decimal
    spine_width_in: Decimal
    bleed_in: Decimal
    spread_width_in: Decimal
    spread_height_in: Decimal
    page_count: int
    spine_text_allowed: bool

    @classmethod
    def from_spec(cls, spec: PrintSpec) -> "CoverGeometry":
        if not spec.is_finalized:
            raise ValueError("print spec must be finalized with page_count and spine_width_in")

        assert spec.page_count is not None
        assert spec.spine_width_in is not None
        spread_width = (
            (spec.trim_width_in * Decimal("2"))
            + spec.spine_width_in
            + (spec.cover_bleed_in * Decimal("2"))
        )
        spread_height = spec.trim_height_in + (spec.cover_bleed_in * Decimal("2"))
        spine_text_allowed = not (
            spec.binding == Binding.PAPERBACK and spec.page_count < 48
        )
        return cls(
            trim_width_in=spec.trim_width_in,
            trim_height_in=spec.trim_height_in,
            spine_width_in=spec.spine_width_in,
            bleed_in=spec.cover_bleed_in,
            spread_width_in=spread_width,
            spread_height_in=spread_height,
            page_count=spec.page_count,
            spine_text_allowed=spine_text_allowed,
        )

    @property
    def fingerprint(self) -> str:
        """Stable identity used to invalidate covers after interior changes."""
        payload = {
            "trim_width_in": str(self.trim_width_in),
            "trim_height_in": str(self.trim_height_in),
            "spine_width_in": str(self.spine_width_in),
            "bleed_in": str(self.bleed_in),
            "page_count": self.page_count,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
