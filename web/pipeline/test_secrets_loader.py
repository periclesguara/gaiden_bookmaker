from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaiden import secrets_loader


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "GAIDEN_ALLOW_LEGACY_SECRETS",
        "GAIDEN_DEFAULT_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_env_var_key_set_does_not_read_files_or_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "secrets_gaiden" / ".env", "OPENAI_API_KEY=sk-from-file\n")
    monkeypatch.setattr(secrets_loader, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(secrets_loader, "_run_openai_healthcheck", lambda: (True, None))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-1234567890")

    def _boom(_path: Path) -> dict[str, str]:
        raise AssertionError("dotenv should not be read when OPENAI_API_KEY is already set")

    monkeypatch.setattr(secrets_loader, "_parse_dotenv", _boom)

    cfg = secrets_loader.bootstrap_openai_env(dry_run=False)
    assert cfg["source"] == "env"
    assert (secrets_loader.os.getenv("OPENAI_API_KEY") or "") == "sk-env-1234567890"


def test_prefers_secrets_gaiden_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path / "secrets_gaiden" / ".env", "OPENAI_API_KEY=sk-file-1234567890\n")
    monkeypatch.setattr(secrets_loader, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(secrets_loader, "_run_openai_healthcheck", lambda: (True, None))

    cfg = secrets_loader.bootstrap_openai_env(dry_run=False)
    assert cfg["source"] == "secrets_gaiden"
    assert (secrets_loader.os.getenv("OPENAI_API_KEY") or "") == "sk-file-1234567890"


def test_missing_secrets_without_legacy_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(secrets_loader, "_repo_root", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="SECRETS_MISSING"):
        secrets_loader.bootstrap_openai_env(dry_run=True)


def test_legacy_allowed_uses_dot_gaiden_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / ".gaiden_secrets", "OPENAI_API_KEY=sk-legacy-1234567890\n")
    monkeypatch.setattr(secrets_loader, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("GAIDEN_ALLOW_LEGACY_SECRETS", "1")
    monkeypatch.setattr(secrets_loader, "_run_openai_healthcheck", lambda: (True, None))

    cfg = secrets_loader.bootstrap_openai_env(dry_run=False)
    assert cfg["source"] == "gaiden_secrets"
    assert (secrets_loader.os.getenv("OPENAI_API_KEY") or "") == "sk-legacy-1234567890"


def test_invalid_key_raises_and_writes_healthcheck_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "secrets_gaiden" / ".env", "OPENAI_API_KEY=sk-bad-1234567890\n")
    monkeypatch.setattr(secrets_loader, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        secrets_loader,
        "_run_openai_healthcheck",
        lambda: (False, "AuthenticationError: invalid_api_key 401"),
    )

    with pytest.raises(RuntimeError, match="OPENAI_HEALTHCHECK_FAILED"):
        secrets_loader.bootstrap_openai_env(dry_run=False)

    report_path = tmp_path / "data" / "contracts_runtime" / "openai_healthcheck_report.json"
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["source"] == "secrets_gaiden"
    assert payload["reason"] == "invalid_api_key"
    assert payload["action"] == "Fix key"

