from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gaiden.env_guard import assert_venv

SECRETS_MISSING_MSG = (
    "SECRETS_MISSING: expected secrets_gaiden/.env (preferred) or env var OPENAI_API_KEY"
)
HEALTHCHECK_REPORT_REL = Path("data/contracts_runtime/openai_healthcheck_report.json")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _normalize_base_url(raw: str | None) -> str | None:
    if not raw:
        return None
    base = raw.strip().rstrip("/")
    if not base:
        return None
    while base.endswith("/v1/v1"):
        base = base[:-3]
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def _parse_dotenv(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def _first_non_empty(*values: str | None) -> str:
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return ""


def _key_prefix(value: str) -> str:
    text = (value or "").strip()
    return text[:10]


def _healthcheck_report_path() -> Path:
    path = _repo_root() / HEALTHCHECK_REPORT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_healthcheck_report(payload: dict[str, Any]) -> Path:
    path = _healthcheck_report_path()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _summarize_error(exc: Exception) -> str:
    msg = str(exc).strip() or repr(exc)
    out = f"{exc.__class__.__name__}: {msg}"
    if len(out) > 240:
        out = out[:237] + "..."
    return out


def _classify_healthcheck_error(error: str | None) -> str:
    text = (error or "").lower()
    if (
        "invalid_api_key" in text
        or "incorrect api key" in text
        or ("authenticationerror" in text and "401" in text)
    ):
        return "invalid_api_key"
    return "unknown"


def _run_openai_healthcheck() -> tuple[bool, str | None]:
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        return False, "missing_openai_api_key"
    base_url = (os.getenv("OPENAI_BASE_URL") or "").strip() or "https://api.openai.com/v1"
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key, base_url=base_url)
        client.responses.create(model="gpt-5-chat-latest", input="ping")
        return True, None
    except Exception as exc:  # pragma: no cover - exercised via tests with monkeypatch
        return False, _summarize_error(exc)


def _resolve_secret_source() -> dict[str, Any]:
    root = _repo_root()
    env_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    env_base = _normalize_base_url((os.getenv("OPENAI_BASE_URL") or "").strip() or None)
    env_model = (os.getenv("GAIDEN_DEFAULT_MODEL") or "gpt-5-chat-latest").strip() or "gpt-5-chat-latest"
    if env_key:
        return {
            "source": "env",
            "api_key": env_key,
            "base_url": env_base,
            "default_model": env_model,
            "secrets_path": None,
            "legacy_allowed": (os.getenv("GAIDEN_ALLOW_LEGACY_SECRETS") or "").strip() == "1",
        }

    preferred_path = root / "secrets_gaiden" / ".env"
    preferred = _parse_dotenv(preferred_path) if preferred_path.exists() else {}
    preferred_key = _first_non_empty(
        preferred.get("OPENAI_API_KEY"),
        preferred.get("GAIDEN_OPENAI_API_KEY"),
        preferred.get("OPENAI_KEY"),
    )
    if preferred_key:
        return {
            "source": "secrets_gaiden",
            "api_key": preferred_key,
            "base_url": _normalize_base_url(
                _first_non_empty(preferred.get("OPENAI_BASE_URL"), preferred.get("GAIDEN_OPENAI_BASE_URL")) or None
            ),
            "default_model": _first_non_empty(preferred.get("GAIDEN_DEFAULT_MODEL"), env_model),
            "secrets_path": str(preferred_path),
            "legacy_allowed": (os.getenv("GAIDEN_ALLOW_LEGACY_SECRETS") or "").strip() == "1",
        }

    legacy_allowed = (os.getenv("GAIDEN_ALLOW_LEGACY_SECRETS") or "").strip() == "1"
    if legacy_allowed:
        legacy_path = root / ".gaiden_secrets"
        legacy = _parse_dotenv(legacy_path) if legacy_path.exists() else {}
        legacy_key = _first_non_empty(
            legacy.get("OPENAI_API_KEY"),
            legacy.get("GAIDEN_OPENAI_API_KEY"),
            legacy.get("OPENAI_KEY"),
        )
        if legacy_key:
            return {
                "source": "gaiden_secrets",
                "api_key": legacy_key,
                "base_url": _normalize_base_url(
                    _first_non_empty(legacy.get("OPENAI_BASE_URL"), legacy.get("GAIDEN_OPENAI_BASE_URL")) or None
                ),
                "default_model": _first_non_empty(legacy.get("GAIDEN_DEFAULT_MODEL"), env_model),
                "secrets_path": str(legacy_path),
                "legacy_allowed": legacy_allowed,
            }

    raise RuntimeError(SECRETS_MISSING_MSG)


def get_openai_config() -> dict[str, Any]:
    cfg = _resolve_secret_source()
    return {
        "source": cfg["source"],
        "api_key": cfg["api_key"],
        "OPENAI_API_KEY": cfg["api_key"],
        "base_url": cfg.get("base_url"),
        "OPENAI_BASE_URL": cfg.get("base_url"),
        "default_model": cfg.get("default_model") or "gpt-5-chat-latest",
        "secrets_path": cfg.get("secrets_path"),
        "legacy_allowed": bool(cfg.get("legacy_allowed")),
        "key_prefix": _key_prefix(cfg["api_key"]),
        "key_len": len((cfg["api_key"] or "").strip()),
    }


def bootstrap_openai_env(*, dry_run: bool = False) -> dict[str, Any]:
    cfg = get_openai_config()

    env_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not env_key:
        file_key = (cfg.get("OPENAI_API_KEY") or "").strip()
        if file_key:
            os.environ["OPENAI_API_KEY"] = file_key
    # Never overwrite non-empty OPENAI_API_KEY and never set it to empty.

    env_base = (os.getenv("OPENAI_BASE_URL") or "").strip()
    if env_base:
        normalized_env_base = _normalize_base_url(env_base)
        if normalized_env_base:
            os.environ["OPENAI_BASE_URL"] = normalized_env_base
    else:
        cfg_base = _normalize_base_url(str(cfg.get("OPENAI_BASE_URL") or "").strip() or None)
        if cfg_base:
            os.environ["OPENAI_BASE_URL"] = cfg_base

    if not dry_run:
        resolved_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not resolved_key:
            raise RuntimeError(SECRETS_MISSING_MSG)
        ok, error = _run_openai_healthcheck()
        if not ok:
            reason = _classify_healthcheck_error(error)
            payload = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "source": cfg.get("source") or "unknown",
                "key_prefix": _key_prefix(resolved_key),
                "key_len": len(resolved_key),
                "ok": False,
                "error": error,
                "reason": reason,
                "action": "Fix key",
            }
            report_path = _write_healthcheck_report(payload)
            raise RuntimeError(
                f"OPENAI_HEALTHCHECK_FAILED: blocked: {reason} ({error}) report={report_path}"
            )

    return cfg


def require_openai_ready(*, dry_run: bool = False, repo_root: str | Path | None = None) -> None:
    if repo_root is not None:
        assert_venv(repo_root)
    bootstrap_openai_env(dry_run=dry_run)


def load_secrets() -> dict[str, Any]:
    return bootstrap_openai_env(dry_run=True)

