"""IngramSpark-compatible print policy.

This adapter models public file-preparation constraints. It does not call or
copy IngramSpark's private backend. Spine width remains an explicit input from
an official template/calculator until a provider-approved coefficient source is
wired into the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..geometry import CoverGeometry
from ..specs import PrintProvider, PrintSpec


@dataclass(frozen=True)
class IngramSparkPolicy:
    cover_bleed_in: Decimal = Decimal("0.125")
    cover_safe_margin_in: Decimal = Decimal("0.25")
    interior_safe_margin_in: Decimal = Decimal("0.5")
    recommended_image_ppi: int = 300
    preferred_pdf_standards: tuple[str, ...] = ("PDF/X-1a:2001", "PDF/X-3:2002")


class IngramSparkAdapter:
    policy = IngramSparkPolicy()

    @classmethod
    def validate_spec(cls, spec: PrintSpec) -> list[str]:
        errors: list[str] = []
        if spec.provider != PrintProvider.INGRAMSPARK:
            errors.append("provider must be ingramspark")
        if spec.cover_bleed_in != cls.policy.cover_bleed_in:
            errors.append(
                f"cover bleed must be {cls.policy.cover_bleed_in} in for IngramSpark"
            )
        if spec.cover_safe_margin_in < cls.policy.cover_safe_margin_in:
            errors.append(
                f"cover safe margin must be at least {cls.policy.cover_safe_margin_in} in"
            )
        if spec.interior_safe_margin_in < cls.policy.interior_safe_margin_in:
            errors.append(
                f"interior safe margin must be at least {cls.policy.interior_safe_margin_in} in"
            )
        for field_name in (
            "inner_margin_in",
            "outer_margin_in",
            "top_margin_in",
            "bottom_margin_in",
        ):
            value = getattr(spec, field_name)
            if value < cls.policy.interior_safe_margin_in:
                errors.append(
                    f"{field_name} must be at least {cls.policy.interior_safe_margin_in} in"
                )
        if spec.interior_bleed:
            errors.append(
                "interior bleed composition is not enabled in print-engine v1; "
                "use a no-bleed interior or the job must remain NO-GO"
            )
        if spec.page_count is None:
            errors.append("final page_count is required before cover generation")
        if spec.spine_width_in is None:
            errors.append(
                "spine_width_in is required; obtain it from the official IngramSpark "
                "cover template/calculator for the finalized interior"
            )
        return errors

    @classmethod
    def cover_geometry(cls, spec: PrintSpec) -> CoverGeometry:
        errors = cls.validate_spec(spec)
        if errors:
            raise ValueError("; ".join(errors))
        return CoverGeometry.from_spec(spec)
