from __future__ import annotations

from pathlib import Path
from typing import Dict

# Arquivo de segredos na raiz do repo
SECRETS_FILE = Path(__file__).resolve().parent.parent / ".gaiden_secrets"

_cache: Dict[str, str] | None = None


def _load_secrets() -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not SECRETS_FILE.is_file():
        return data

    for line in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def get_openai_key() -> str | None:
    """
    Lê OPENAI_API_KEY do arquivo .gaiden_secrets.
    Formato esperado:
        OPENAI_API_KEY=sk-xxxxx
    """
    global _cache
    if _cache is None:
        _cache = _load_secrets()
    return _cache.get("OPENAI_API_KEY")


def set_openai_key(key: str) -> None:
    """
    Salva/atualiza OPENAI_API_KEY em .gaiden_secrets.
    Se key vier vazia, remove a chave do arquivo.
    """
    global _cache
    data = _load_secrets()
    clean = (key or "").strip()
    if clean:
        data["OPENAI_API_KEY"] = clean
    else:
        data.pop("OPENAI_API_KEY", None)

    lines = [f"{k}={v}" for k, v in data.items()]
    SECRETS_FILE.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    _cache = data
