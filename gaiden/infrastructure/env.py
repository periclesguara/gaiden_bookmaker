from __future__ import annotations

import os
from pathlib import Path

from .storage import repo_root

SECRETS_FILE_ENV = "GAIDEN_SECRETS_FILE"
DEFAULT_SECRETS_FILENAME = ".gaiden_secrets"

_cache: dict[str, str] | None = None


class MissingRequiredSecret(RuntimeError):
    pass


def secrets_file() -> Path:
    configured = (os.environ.get(SECRETS_FILE_ENV) or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = repo_root() / path
        return path.resolve()
    return repo_root() / DEFAULT_SECRETS_FILENAME


def load_repo_secrets(*, force_reload: bool = False) -> dict[str, str]:
    global _cache
    if _cache is not None and not force_reload:
        return dict(_cache)

    path = secrets_file()
    data: dict[str, str] = {}
    if path.is_file():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
    _cache = data
    return dict(data)


def get_secret(name: str, default: str | None = None, *, prefer_env: bool = False) -> str | None:
    if prefer_env:
        value = os.environ.get(name)
        if value:
            return value
    file_value = load_repo_secrets().get(name)
    if file_value:
        return file_value
    env_value = os.environ.get(name)
    if env_value:
        return env_value
    return default


def require_secret(name: str, *, prefer_env: bool = False) -> str:
    value = get_secret(name, prefer_env=prefer_env)
    if value:
        return value
    raise MissingRequiredSecret(
        f"Required secret {name} not found in {secrets_file()} or environment."
    )


def set_secret(name: str, value: str) -> None:
    data = load_repo_secrets(force_reload=True)
    cleaned = (value or "").strip()
    if cleaned:
        data[name] = cleaned
    else:
        data.pop(name, None)

    lines = [f"{key}={raw}" for key, raw in data.items()]
    path = secrets_file()
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    load_repo_secrets(force_reload=True)


def get_openai_api_key() -> str | None:
    return get_secret("OPENAI_API_KEY")


def require_openai_api_key() -> str:
    return require_secret("OPENAI_API_KEY")
