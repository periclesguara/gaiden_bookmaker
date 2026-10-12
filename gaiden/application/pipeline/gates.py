from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gaiden.application.pipeline.status import validate_artifact


@dataclass(frozen=True)
class GateResult:
    ok: bool
    reason: str = ""


def require_existing_file(
    path_value: str | Path,
    *,
    min_size_bytes: int = 1,
    missing_reason: str,
    invalid_reason: str | None = None,
) -> GateResult:
    check = validate_artifact(path_value, min_size_bytes=min_size_bytes)
    if check.valid:
        return GateResult(True, "")
    if check.reason == "missing":
        return GateResult(False, missing_reason)
    return GateResult(False, invalid_reason or missing_reason)


def preflight_gate(*, editorial_ready: bool, merge_refine_clean_path: str | Path) -> GateResult:
    if not editorial_ready:
        return GateResult(
            False,
            "Prerequisito: conclua o Bloco 03 com Frontispiece, Copyright e About This Book.",
        )
    return require_existing_file(
        merge_refine_clean_path,
        missing_reason="Prerequisito: rode Merge/Finalize e gere merge_refine_clean.txt.",
        invalid_reason="Prerequisito: merge_refine_clean.txt existe, mas esta vazio ou invalido.",
    )
