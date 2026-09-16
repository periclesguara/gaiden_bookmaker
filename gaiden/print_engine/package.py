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

    def __post_init__(self) -> None:
        if not isinstance(self.book_id, str) or not self.book_id.strip():
            raise ValueError("book_id must not be empty")

        expected_geometry = CoverGeometry.from_spec(self.spec)
        if self.geometry != expected_geometry:
            raise ValueError("cover geometry does not match the finalized print spec")

        for field_name in ("interior_pdf", "cover_pdf"):
            filename = getattr(self, field_name)
            if not isinstance(filename, str):
                raise ValueError(f"{field_name} must be a safe PDF filename")
            path = Path(filename)
            if (
                not filename
                or "/" in filename
                or "\\" in filename
                or path.name != filename
                or not path.stem
                or path.suffix.lower() != ".pdf"
            ):
                raise ValueError(f"{field_name} must be a safe PDF filename")
        if self.interior_pdf == self.cover_pdf:
            raise ValueError("interior and cover PDF filenames must be different")

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
