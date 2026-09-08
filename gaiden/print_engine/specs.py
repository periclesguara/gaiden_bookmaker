"""Canonical print-edition specifications.

All physical dimensions are stored in inches as Decimal values.  Keeping the
spec immutable makes print artefacts reproducible: a change to trim, paper,
page count or spine width creates a different edition fingerprint rather than
silently mutating an already approved cover.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from typing import Optional


class PrintProvider(str, Enum):
    INGRAMSPARK = "ingramspark"
    LULU = "lulu"
    GENERIC = "generic"


class Binding(str, Enum):
    PAPERBACK = "paperback"
    HARDCOVER = "hardcover"


class Paper(str, Enum):
    CREAM = "cream"
    WHITE = "white"
    COLOR_WHITE = "color_white"


class InteriorColor(str, Enum):
    BLACK_AND_WHITE = "black_and_white"
    COLOR = "color"


def _d(value: Decimal | str | int | float) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(frozen=True)
class PrintSpec:
    provider: PrintProvider = PrintProvider.INGRAMSPARK
    trim_width_in: Decimal = Decimal("6")
    trim_height_in: Decimal = Decimal("9")
    binding: Binding = Binding.PAPERBACK
    paper: Paper = Paper.CREAM
    interior_color: InteriorColor = InteriorColor.BLACK_AND_WHITE
    interior_bleed: bool = False

    # Provider/layout policy. These defaults match the Ingram-compatible
    # adapter, but adapters remain responsible for validating them.
    cover_bleed_in: Decimal = Decimal("0.125")
    interior_safe_margin_in: Decimal = Decimal("0.5")
    cover_safe_margin_in: Decimal = Decimal("0.25")

    body_font_size_pt: Decimal = Decimal("11")
    body_leading_pt: Decimal = Decimal("14")
    body_font: str = "TeX Gyre Pagella"

    # These values are intentionally absent until the interior is final.  We
    # do not guess provider paper-caliper coefficients in the core engine.
    page_count: Optional[int] = None
    spine_width_in: Optional[Decimal] = None

    def __post_init__(self) -> None:
        decimal_fields = (
            "trim_width_in",
            "trim_height_in",
            "cover_bleed_in",
            "interior_safe_margin_in",
            "cover_safe_margin_in",
            "body_font_size_pt",
            "body_leading_pt",
        )
        for field_name in decimal_fields:
            object.__setattr__(self, field_name, _d(getattr(self, field_name)))
        if self.spine_width_in is not None:
            object.__setattr__(self, "spine_width_in", _d(self.spine_width_in))

        if self.trim_width_in <= 0 or self.trim_height_in <= 0:
            raise ValueError("trim dimensions must be positive")
        if self.cover_bleed_in < 0:
            raise ValueError("cover bleed cannot be negative")
        if self.interior_safe_margin_in <= 0 or self.cover_safe_margin_in <= 0:
            raise ValueError("safe margins must be positive")
        if self.body_font_size_pt <= 0 or self.body_leading_pt <= 0:
            raise ValueError("font size and leading must be positive")
        if self.body_leading_pt < self.body_font_size_pt:
            raise ValueError("body leading cannot be smaller than font size")
        if self.page_count is not None and self.page_count <= 0:
            raise ValueError("page count must be positive")
        if self.spine_width_in is not None and self.spine_width_in <= 0:
            raise ValueError("spine width must be positive")

    @property
    def is_finalized(self) -> bool:
        return self.page_count is not None and self.spine_width_in is not None

    def finalize(self, *, page_count: int, spine_width_in: Decimal | str | float) -> "PrintSpec":
        """Return a new immutable spec bound to the final interior geometry."""
        return replace(
            self,
            page_count=int(page_count),
            spine_width_in=_d(spine_width_in),
        )
