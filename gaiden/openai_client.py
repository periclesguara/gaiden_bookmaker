from __future__ import annotations

import json
import os
from pathlib import Path

from openai import OpenAI

CONFIG_PATH = Path.home() / ".config" / "gaiden" / "credentials.json"


def load_api_key() -> str:
    """
    Ordem de prioridade:
    1) Variável de ambiente OPENAI_API_KEY
    2) Arquivo ~/.config/gaiden/credentials.json
       com campo OPENAI_API_KEY ou openai_api_key
    """
    env_key = os.environ.get("OPENAI_API_KEY")
    if env_key:
        return env_key

    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        file_key = data.get("OPENAI_API_KEY") or data.get("openai_api_key")
        if file_key:
            return file_key

    raise RuntimeError(
        "OpenAI API key não encontrada. "
        "Defina OPENAI_API_KEY no ambiente ou crie "
        "~/.config/gaiden/credentials.json com OPENAI_API_KEY."
    )


def get_client() -> OpenAI:
    """
    Retorna um cliente OpenAI configurado.
    """
    api_key = load_api_key()
    return OpenAI(api_key=api_key)
