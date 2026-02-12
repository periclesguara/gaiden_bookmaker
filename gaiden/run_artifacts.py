from __future__ import annotations

import json
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def create_run_dir(runs_root: Path, prefix: str) -> tuple[Path, str]:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    run_id = f"{prefix}_{ts}"
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir, run_id


def _git_sha(repo_root: Path) -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True)
            .strip()
        )
    except Exception:
        return "unknown"


def write_env_json(
    run_dir: Path,
    *,
    dry_run: bool,
    model: str | None,
    base_url: str | None,
    repo_root: Path,
    extra: dict[str, Any] | None = None,
) -> Path:
    data: dict[str, Any] = {
        "schema": "gaiden_env_v1",
        "created_at": _utc_now(),
        "python_executable": sys.executable,
        "git_sha": _git_sha(repo_root),
        "dry_run": bool(dry_run),
        "model_effective": model,
        "base_url_effective": base_url,
    }
    if extra:
        data.update(extra)
    out = run_dir / "env.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def write_contract_json(run_dir: Path, contract: dict[str, Any]) -> Path:
    out = run_dir / "contract.json"
    out.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
