from __future__ import annotations

from pathlib import Path
from typing import Optional

# Arquivo local, fora do Git, para guardar a chave.
SECRET_FILE = Path(".gaiden_secrets")


def get_openai_key() -> Optional[str]:
    """
    Lê a chave OPENAI_API_KEY do arquivo .gaiden_secrets, se existir.
    Formato esperado:
        OPENAI_API_KEY=sk-xxxx...
    """
    if not SECRET_FILE.exists():
        return None

    try:
        text = SECRET_FILE.read_text(encoding="utf-8")
    except Exception:
        return None

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("OPENAI_API_KEY="):
            return line.split("=", 1)[1].strip() or None
    return None


def set_openai_key(key: str) -> None:
    """
    Salva/atualiza a chave no arquivo .gaiden_secrets.
    Sobrescreve qualquer valor anterior.
    """
    key = (key or "").strip()
    if not key:
        # Se quiser permitir "limpar" a chave, pode trocar por SECRET_FILE.unlink(missing_ok=True)
        SECRET_FILE.write_text("", encoding="utf-8")
        return

    SECRET_FILE.write_text(f"OPENAI_API_KEY={key}\n", encoding="utf-8")
