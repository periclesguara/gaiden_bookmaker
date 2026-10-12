"""Full-wrap cover panel layout derived from finalized print geometry."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .geometry import CoverGeometry
from .specs import PrintSpec


@dataclass(frozen=True)
class Rect:
    """Rectangle in inches, measured from the lower-left of the cover canvas."""

    x: Decimal
    y: Decimal
    width: Decimal
    height: Decimal

    def inset(self, amount: Decimal) -> "Rect":
        if amount < 0 or amount * 2 >= min(self.width, self.height):
            raise ValueError("invalid inset")
        return Rect(
            x=self.x + amount,
            y=self.y + amount,
            width=self.width - amount * 2,
            height=self.height - amount * 2,
        )


@dataclass(frozen=True)
class CoverLayout:
    canvas: Rect
    back: Rect
    spine: Rect
    front: Rect
    back_safe: Rect
    front_safe: Rect
    geometry_fingerprint: str

    @classmethod
    def from_spec(cls, spec: PrintSpec) -> "CoverLayout":
        geometry = CoverGeometry.from_spec(spec)
        bleed = geometry.bleed_in
        safe = spec.cover_safe_margin_in

        canvas = Rect(Decimal("0"), Decimal("0"), geometry.spread_width_in, geometry.spread_height_in)
        back = Rect(bleed, bleed, geometry.trim_width_in, geometry.trim_height_in)
        spine = Rect(
            bleed + geometry.trim_width_in,
            bleed,
            geometry.spine_width_in,
            geometry.trim_height_in,
        )
        front = Rect(
            bleed + geometry.trim_width_in + geometry.spine_width_in,
            bleed,
            geometry.trim_width_in,
            geometry.trim_height_in,
        )
        return cls(
            canvas=canvas,
            back=back,
            spine=spine,
            front=front,
            back_safe=back.inset(safe),
            front_safe=front.inset(safe),
            geometry_fingerprint=geometry.fingerprint,
        )
