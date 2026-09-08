"""Manifest for reproducible print-release packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .geometry import CoverGeometry
from .specs import PrintSpec


@dataclass(frozen=True)
class PrintPackageManifest:
    book_id: str
    spec: PrintSpec
    geometry: CoverGeometry
    interior_pdf: str = "INTERIOR.pdf"
    cover_pdf: str = "COVER.pdf"

    def as_dict(self) -> dict[str, object]:
        return {
            "book_id": self.book_id,
            "provider": self.spec.provider.value,
            "binding": self.spec.binding.value,
            "paper": self.spec.paper.value,
            "interior_color": self.spec.interior_color.value,
            "trim": {
                "width_in": str(self.spec.trim_width_in),
                "height_in": str(self.spec.trim_height_in),
            },
            "page_count": self.spec.page_count,
            "spine_width_in": str(self.spec.spine_width_in) if self.spec.spine_width_in is not None else None,
            "cover": {
                "spread_width_in": str(self.geometry.spread_width_in),
                "spread_height_in": str(self.geometry.spread_height_in),
                "bleed_in": str(self.geometry.bleed_in),
                "spine_text_allowed": self.geometry.spine_text_allowed,
                "geometry_fingerprint": self.geometry.fingerprint,
            },
            "artifacts": {
                "interior": self.interior_pdf,
                "cover": self.cover_pdf,
            },
        }

    def artifact_paths(self, package_dir: str | Path) -> tuple[Path, Path]:
        root = Path(package_dir)
        return root / self.interior_pdf, root / self.cover_pdf
