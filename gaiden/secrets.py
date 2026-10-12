from __future__ import annotations

"""Compatibility wrapper. New code should import gaiden.infrastructure.env."""

from gaiden.infrastructure.env import (
    load_repo_secrets,
    secrets_file,
)

SECRETS_FILE = secrets_file()


def _load_secrets() -> dict[str, str]:
    return load_repo_secrets()
