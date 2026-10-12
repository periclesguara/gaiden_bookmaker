"""Preflight checks for print specifications and generated PDF artefacts."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path

from .geometry import CoverGeometry
from .providers.ingramspark import IngramSparkAdapter
from .specs import PrintProvider, PrintSpec


class PreflightStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    SKIP = "skip"


@dataclass(frozen=True)
class PreflightCheck:
    code: str
    status: PreflightStatus
    message: str


@dataclass(frozen=True)
class PreflightReport:
    checks: tuple[PreflightCheck, ...]

    @property
    def passed(self) -> bool:
        """Whether the automated checks found no release-blocking failure.

        A passing report can still require review when a check is WARN or SKIP.
        Use ``fully_verified`` when every requested automated check must have
        completed successfully.
        """
        return all(check.status != PreflightStatus.FAIL for check in self.checks)

    @property
    def fully_verified(self) -> bool:
        return bool(self.checks) and all(
            check.status == PreflightStatus.PASS for check in self.checks
        )

    @property
    def requires_review(self) -> bool:
        return any(
            check.status in (PreflightStatus.WARN, PreflightStatus.SKIP)
            for check in self.checks
        )

    @property
    def failures(self) -> tuple[PreflightCheck, ...]:
        return tuple(check for check in self.checks if check.status == PreflightStatus.FAIL)

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "fully_verified": self.fully_verified,
            "requires_review": self.requires_review,
            "checks": [
                {"code": c.code, "status": c.status.value, "message": c.message}
                for c in self.checks
            ],
        }


def preflight_spec(spec: PrintSpec) -> PreflightReport:
    checks: list[PreflightCheck] = []
    if spec.provider == PrintProvider.INGRAMSPARK:
        errors = IngramSparkAdapter.validate_spec(spec)
    else:
        errors = [f"provider adapter not implemented: {spec.provider.value}"]

    if errors:
        checks.extend(
            PreflightCheck("spec.provider_policy", PreflightStatus.FAIL, message)
            for message in errors
        )
    else:
        checks.append(
            PreflightCheck(
                "spec.provider_policy",
                PreflightStatus.PASS,
                "print specification satisfies provider policy",
            )
        )
    return PreflightReport(tuple(checks))


def _run_tool(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _pdfinfo(path: Path) -> tuple[str | None, str | None, bool]:
    if shutil.which("pdfinfo") is None:
        return None, "pdfinfo is not installed", True
    try:
        result = _run_tool(["pdfinfo", str(path)])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"pdfinfo could not inspect {path}: {exc}", False
    if result.returncode != 0:
        return None, result.stderr.strip() or "pdfinfo failed", False
    return result.stdout, None, False


def _parse_page_count(pdfinfo_output: str) -> int | None:
    match = re.search(r"^Pages:\s+(\d+)\s*$", pdfinfo_output, flags=re.MULTILINE)
    return int(match.group(1)) if match else None


def inspect_pdf_page_count(path: str | Path) -> int:
    """Return a verified PDF page count using the local Poppler toolchain."""
    pdf_path = Path(path)
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    output, error, unavailable = _pdfinfo(pdf_path)
    if unavailable:
        raise RuntimeError(error or "pdfinfo is required to inspect the final interior")
    if output is None:
        raise ValueError(error or f"could not inspect PDF: {pdf_path}")
    page_count = _parse_page_count(output)
    if page_count is None:
        raise ValueError(f"pdfinfo did not report a page count for {pdf_path}")
    return page_count


def _parse_page_size_in(pdfinfo_output: str) -> tuple[Decimal, Decimal] | None:
    match = re.search(
        r"^Page size:\s+([0-9.]+)\s+x\s+([0-9.]+)\s+pts",
        pdfinfo_output,
        flags=re.MULTILINE,
    )
    if not match:
        return None
    points_per_inch = Decimal("72")
    return Decimal(match.group(1)) / points_per_inch, Decimal(match.group(2)) / points_per_inch


def _close_enough(actual: Decimal, expected: Decimal, *, tolerance: Decimal = Decimal("0.01")) -> bool:
    return abs(actual - expected) <= tolerance


def _fonts_embedded(path: Path) -> tuple[bool | None, str | None, bool]:
    if shutil.which("pdffonts") is None:
        return None, "pdffonts is not installed", True
    try:
        result = _run_tool(["pdffonts", str(path)])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"pdffonts could not inspect {path}: {exc}", False
    if result.returncode != 0:
        return None, result.stderr.strip() or "pdffonts failed", False

    font_rows = []
    for line in result.stdout.splitlines():
        match = re.search(
            r"\s+(yes|no)\s+(yes|no)\s+(yes|no)\s+\d+\s+\d+\s*$",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            font_rows.append(match.group(1).lower() == "yes")
    if not font_rows:
        # A valid image/vector-only PDF can legitimately contain no fonts.
        # pdffonts still emits its two-line table header in that case.
        if "emb" in result.stdout.lower() and "object id" in result.stdout.lower():
            return True, None, False
        return None, "pdffonts returned an unrecognized result", False
    return all(font_rows), None, False


def _append_size_check(
    checks: list[PreflightCheck],
    *,
    code: str,
    actual: tuple[Decimal, Decimal] | None,
    expected: tuple[Decimal, Decimal],
) -> None:
    if actual is None:
        checks.append(PreflightCheck(code, PreflightStatus.WARN, "PDF page size was not reported"))
        return
    if _close_enough(actual[0], expected[0]) and _close_enough(actual[1], expected[1]):
        checks.append(
            PreflightCheck(
                code,
                PreflightStatus.PASS,
                f"page size verified: {actual[0]:.3f} x {actual[1]:.3f} in",
            )
        )
    else:
        checks.append(
            PreflightCheck(
                code,
                PreflightStatus.FAIL,
                "page size mismatch: "
                f"actual {actual[0]:.3f} x {actual[1]:.3f} in; "
                f"expected {expected[0]:.3f} x {expected[1]:.3f} in",
            )
        )


def preflight_pdfs(
    spec: PrintSpec,
    *,
    interior_pdf: str | Path,
    cover_pdf: str | Path,
) -> PreflightReport:
    """Validate provider policy plus checks available from local PDF tools.

    External tools are optional. Their absence yields WARN rather than PASS so
    a release operator can distinguish "verified" from "not inspected".
    """
    checks = list(preflight_spec(spec).checks)
    interior = Path(interior_pdf)
    cover = Path(cover_pdf)

    for code, path in (("interior.exists", interior), ("cover.exists", cover)):
        checks.append(
            PreflightCheck(
                code,
                PreflightStatus.PASS if path.is_file() else PreflightStatus.FAIL,
                f"{path} exists" if path.is_file() else f"missing PDF: {path}",
            )
        )

    if not interior.is_file() or not cover.is_file():
        return PreflightReport(tuple(checks))

    interior_info, interior_error, interior_info_skipped = _pdfinfo(interior)
    cover_info, cover_error, cover_info_skipped = _pdfinfo(cover)

    if interior_info is None:
        checks.append(
            PreflightCheck(
                "interior.pdfinfo",
                PreflightStatus.SKIP if interior_info_skipped else PreflightStatus.FAIL,
                interior_error or "interior PDF not inspected",
            )
        )
    else:
        pages = _parse_page_count(interior_info)
        if pages is None:
            checks.append(
                PreflightCheck("interior.page_count", PreflightStatus.WARN, "pdfinfo did not report a page count")
            )
        elif spec.page_count is not None and pages != spec.page_count:
            checks.append(
                PreflightCheck(
                    "interior.page_count",
                    PreflightStatus.FAIL,
                    f"interior has {pages} pages but finalized spec expects {spec.page_count}",
                )
            )
        else:
            checks.append(
                PreflightCheck("interior.page_count", PreflightStatus.PASS, f"interior page count verified: {pages}")
            )
        _append_size_check(
            checks,
            code="interior.page_size",
            actual=_parse_page_size_in(interior_info),
            expected=(spec.trim_width_in, spec.trim_height_in),
        )

    if cover_info is None:
        checks.append(
            PreflightCheck(
                "cover.pdfinfo",
                PreflightStatus.SKIP if cover_info_skipped else PreflightStatus.FAIL,
                cover_error or "cover PDF not inspected",
            )
        )
    elif spec.is_finalized:
        cover_pages = _parse_page_count(cover_info)
        if cover_pages == 1:
            checks.append(PreflightCheck("cover.page_count", PreflightStatus.PASS, "cover is a single-page spread"))
        else:
            checks.append(
                PreflightCheck(
                    "cover.page_count",
                    PreflightStatus.FAIL,
                    f"cover must be one page; detected {cover_pages if cover_pages is not None else 'unknown'}",
                )
            )
        geometry = CoverGeometry.from_spec(spec)
        _append_size_check(
            checks,
            code="cover.page_size",
            actual=_parse_page_size_in(cover_info),
            expected=(geometry.spread_width_in, geometry.spread_height_in),
        )

    for code, path in (("interior.fonts_embedded", interior), ("cover.fonts_embedded", cover)):
        embedded, font_error, font_check_skipped = _fonts_embedded(path)
        if embedded is None:
            checks.append(
                PreflightCheck(
                    code,
                    PreflightStatus.SKIP if font_check_skipped else PreflightStatus.FAIL,
                    font_error or "font embedding not verified",
                )
            )
        elif embedded:
            checks.append(PreflightCheck(code, PreflightStatus.PASS, "all detected fonts are embedded"))
        else:
            checks.append(PreflightCheck(code, PreflightStatus.FAIL, "one or more fonts are not embedded"))

    checks.append(
        PreflightCheck(
            "pdfx.validation",
            PreflightStatus.WARN,
            "PDF/X conformance is not yet machine-verified in v1; run the provider proof/preflight before release",
        )
    )
    return PreflightReport(tuple(checks))
