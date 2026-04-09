from __future__ import annotations

"""Compatibility wrapper. New code should import gaiden.infrastructure.env."""

from gaiden.infrastructure.env import (
    get_openai_api_key,
    load_repo_secrets,
    secrets_file,
    set_secret,
)

SECRETS_FILE = secrets_file()


def _load_secrets() -> dict[str, str]:
    return load_repo_secrets()


def get_openai_key() -> str | None:
    """
    Lê OPENAI_API_KEY da camada central de segredos.
    Preserva compatibilidade com importadores antigos de gaiden.secrets.
    """
    return get_openai_api_key()


def set_openai_key(key: str) -> None:
    """
    Salva/atualiza OPENAI_API_KEY via loader central de segredos.
    """
    set_secret("OPENAI_API_KEY", key)
