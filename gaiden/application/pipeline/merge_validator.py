from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MergeValidationError:
    type: str
    source_chunk_id: str = ""
    path: str = ""
    field: str = ""
    expected: str = ""
    observed: str = ""


def _error(type_: str, **kwargs: Any) -> dict[str, str]:
    return asdict(MergeValidationError(type=type_, **{k: str(v) for k, v in kwargs.items()}))


def validate_manifest_driven_outputs(
    *,
    expected: list[dict[str, Any]],
    received: list[dict[str, Any]],
    book_code: str,
    language: str,
    stage: str,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    expected_by_id = {str(row["source_chunk_id"]): row for row in expected}
    seen: dict[str, list[dict[str, Any]]] = {}

    for row in received:
        source_chunk_id = str(row.get("source_chunk_id") or "")
        if not source_chunk_id:
            errors.append(_error("WRONG_SOURCE_CHUNK_ID", path=row.get("path", "")))
            continue
        seen.setdefault(source_chunk_id, []).append(row)
        expected_row = expected_by_id.get(source_chunk_id)
        if expected_row is None:
            errors.append(_error("EXTRA_CHUNK", source_chunk_id=source_chunk_id, path=row.get("path", "")))
            continue
        checks = (
            ("book_code", book_code),
            ("language", language),
            ("stage", stage),
            ("chapter_index", expected_row.get("chapter_index")),
            ("chunk_index", expected_row.get("chunk_index")),
        )
        for field, expected_value in checks:
            observed = row.get(field)
            if str(observed) != str(expected_value):
                errors.append(
                    _error(
                        "WRONG_" + field.upper(),
                        source_chunk_id=source_chunk_id,
                        path=row.get("path", ""),
                        field=field,
                        expected=expected_value,
                        observed=observed,
                    )
                )

    for source_chunk_id in expected_by_id:
        if source_chunk_id not in seen:
            errors.append(_error("MISSING_CHUNK", source_chunk_id=source_chunk_id))
    for source_chunk_id, rows in seen.items():
        if len(rows) > 1:
            errors.append(
                _error(
                    "DUPLICATE_CHUNK",
                    source_chunk_id=source_chunk_id,
                    path=", ".join(str(row.get("path") or "") for row in rows),
                )
            )

    manifest_order = [str(row["source_chunk_id"]) for row in expected]
    received_order = [str(row.get("source_chunk_id") or "") for row in received if str(row.get("source_chunk_id") or "") in expected_by_id]
    if received_order != [item for item in manifest_order if item in received_order]:
        errors.append(_error("FILES_OUT_OF_MANIFEST_ORDER", observed=",".join(received_order)))

    return {"ok": not errors, "errors": errors}


def write_json_report(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
