from __future__ import annotations

import os
from pathlib import Path

from gaiden.env_guard import assert_venv
from gaiden.net_preflight import preflight_openai


def _normalize_base_url(raw: str | None) -> str | None:
    if not raw:
        return None
    base = raw.strip().rstrip("/")
    # Collapse accidental duplicated /v1 segments.
    while base.endswith("/v1/v1"):
        base = base[:-3]
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def _parse_dotenv(path: Path) -> dict:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        data[k] = v
    return data


def get_openai_config() -> dict:
    """
    Single source of truth:
      - Reads repo-root/.gaiden_secrets
      - Does NOT depend on cwd
      - Maps OPENAI_API_KEY / OPENAI_BASE_URL (and compatible aliases)
    """
    root = Path(__file__).resolve().parents[1]
    secrets_path = root / ".gaiden_secrets"
    secrets = _parse_dotenv(secrets_path)

    api_key = (
        secrets.get("OPENAI_API_KEY")
        or secrets.get("GAIDEN_OPENAI_API_KEY")
        or secrets.get("OPENAI_KEY")
        or ""
    ).strip()

    base_url = (
        secrets.get("OPENAI_BASE_URL")
        or secrets.get("GAIDEN_OPENAI_BASE_URL")
        or ""
    ).strip() or None
    base_url = _normalize_base_url(base_url)

    default_model = (secrets.get("GAIDEN_DEFAULT_MODEL") or "gpt-5-chat-latest").strip() or "gpt-5-chat-latest"

    return {
        "api_key": api_key,
        "OPENAI_API_KEY": api_key,  # alias pra legado
        "base_url": base_url,
        "OPENAI_BASE_URL": base_url,
        "default_model": default_model,
        "secrets_path": str(secrets_path),
    }


def bootstrap_openai_env(*, dry_run: bool = False) -> dict:
    """
    Canonical bootstrap:
      - Reads .gaiden_secrets
      - Normalizes OPENAI_BASE_URL to end with /v1 (exactly once)
      - Populates os.environ for OPENAI_API_KEY / OPENAI_BASE_URL
    Returns the effective config dict.
    """
    cfg = get_openai_config()
    if cfg.get("OPENAI_API_KEY") is not None:
        os.environ["OPENAI_API_KEY"] = cfg["OPENAI_API_KEY"]
    if cfg.get("OPENAI_BASE_URL") is not None:
        os.environ["OPENAI_BASE_URL"] = cfg["OPENAI_BASE_URL"]
    else:
        os.environ.pop("OPENAI_BASE_URL", None)
    # default_model is returned in cfg; avoid exporting GAIDEN_DEFAULT_MODEL implicitly.

    if not dry_run and not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY AUSENTE — necessário para execução real")
    if not dry_run:
        preflight_openai(os.environ.get("OPENAI_BASE_URL"))
    return cfg


def require_openai_ready(*, dry_run: bool = False, repo_root: str | Path | None = None) -> None:
    """
    Enforce readiness consistently across UI/CLI.
    """
    if repo_root is not None:
        assert_venv(repo_root)
    bootstrap_openai_env(dry_run=dry_run)


def load_secrets() -> dict:
    """
    Backwards-compatible wrapper for legacy callers.
    """
    return bootstrap_openai_env(dry_run=True)
