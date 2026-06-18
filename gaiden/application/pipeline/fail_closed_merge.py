from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import boundary_validator


def failed_merge_dir(*, root: Path, book_code: str, language: str, stage: str, run_id: str) -> Path:
    return root / "data" / "failed_merges" / book_code / language / stage / run_id


def write_failure_artifacts(
    *,
    root: Path,
    book_code: str,
    language: str,
    stage: str,
    run_id: str,
    merge_validation: dict[str, Any],
    boundary_validation: dict[str, Any],
    chunk_order_report: dict[str, Any],
    preview_text: str,
) -> Path:
    out_dir = failed_merge_dir(root=root, book_code=book_code, language=language, stage=stage, run_id=run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "merge_validation_report.json").write_text(
        json.dumps(merge_validation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "boundary_validation_report.json").write_text(
        json.dumps(boundary_validation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "chunk_order_report.json").write_text(
        json.dumps(chunk_order_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "failed_merge_preview.txt").write_text(preview_text[:20000], encoding="utf-8")
    diagnostics = {
        "book_code": book_code,
        "language": language,
        "stage": stage,
        "run_id": run_id,
        "merge_ok": bool(merge_validation.get("ok")),
        "boundary_ok": bool(boundary_validation.get("ok")),
        "final_status": "FAILED",
        "canonical_written": False,
        "reports": {
            "merge_validation_report": str(out_dir / "merge_validation_report.json"),
            "boundary_validation_report": str(out_dir / "boundary_validation_report.json"),
            "chunk_order_report": str(out_dir / "chunk_order_report.json"),
        },
    }
    (out_dir / "failed_merge_diagnostics.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out_dir


def validate_repair_and_write(
    *,
    text: str,
    out_path: Path,
    root: Path,
    book_code: str,
    language: str,
    stage: str,
    run_id: str,
    merge_validation: dict[str, Any],
    chunk_order_report: dict[str, Any],
    allow_auto_repair: bool = True,
) -> tuple[Path, dict[str, Any]]:
    boundary_report = boundary_validator.validate_boundaries(text)
    repairs: list[dict[str, Any]] = []
    final_text = text
    if not boundary_report["ok"] and allow_auto_repair:
        repaired, repairs = boundary_validator.auto_repair_boundaries(text)
        repaired_report = boundary_validator.validate_boundaries(repaired)
        if repaired_report["ok"]:
            final_text = repaired
            boundary_report = repaired_report
    boundary_report = {
        **boundary_report,
        "auto_repairs_applied": repairs,
        "book_code": book_code,
        "language": language,
        "stage": stage,
        "run_id": run_id,
        "final_status": "PASSED" if boundary_report["ok"] else "FAILED",
        "canonical_written": bool(boundary_report["ok"] and merge_validation.get("ok", True)),
    }

    if not merge_validation.get("ok", True) or not boundary_report["ok"]:
        failure_dir = write_failure_artifacts(
            root=root,
            book_code=book_code,
            language=language,
            stage=stage,
            run_id=run_id,
            merge_validation=merge_validation,
            boundary_validation=boundary_report,
            chunk_order_report=chunk_order_report,
            preview_text=final_text,
        )
        raise ValueError(f"{stage} merge validation failed; canonical not overwritten: {failure_dir}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(final_text, encoding="utf-8")
    shutil.move(str(tmp_path), str(out_path))
    report_dir = out_path.parent
    (report_dir / "boundary_validation_report.json").write_text(
        json.dumps(boundary_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out_path, boundary_report
