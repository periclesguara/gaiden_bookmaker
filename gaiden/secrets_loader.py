from __future__ import annotations

from pathlib import Path


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

    default_model = (secrets.get("GAIDEN_DEFAULT_MODEL") or "gpt5-chat-latest").strip() or "gpt5-chat-latest"

    return {
        "api_key": api_key,
        "OPENAI_API_KEY": api_key,  # alias pra legado
        "base_url": base_url,
        "OPENAI_BASE_URL": base_url,
        "default_model": default_model,
        "secrets_path": str(secrets_path),
    }
