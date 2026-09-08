"""Gaiden print/prepress engine.

Pure, deterministic primitives for composing print editions and validating
provider-specific print packages. The package intentionally has no Django or
provider API dependency so it can be used by the web pipeline, CLI jobs and
future release adapters.
"""

from .compiler import LatexCompileResult, LatexCompilerUnavailable, compile_lualatex
from .cover import CoverLayout, Rect
from .geometry import CoverGeometry
from .latex import BookContent, Chapter, LatexInteriorRenderer
from .package import PrintPackageManifest
from .preflight import (
    PreflightCheck,
    PreflightReport,
    PreflightStatus,
    preflight_pdfs,
    preflight_spec,
)
from .specs import Binding, InteriorColor, Paper, PrintProvider, PrintSpec

__all__ = [
    "Binding",
    "BookContent",
    "Chapter",
    "CoverGeometry",
    "CoverLayout",
    "InteriorColor",
    "LatexCompileResult",
    "LatexCompilerUnavailable",
    "LatexInteriorRenderer",
    "Paper",
    "PreflightCheck",
    "PreflightReport",
    "PreflightStatus",
    "PrintPackageManifest",
    "PrintProvider",
    "PrintSpec",
    "Rect",
    "compile_lualatex",
    "preflight_pdfs",
    "preflight_spec",
]
