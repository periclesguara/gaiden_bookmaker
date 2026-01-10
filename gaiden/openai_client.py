from __future__ import annotations

import os
from typing import Optional

from openai import OpenAI
from gaiden.secrets import get_openai_key

_client: Optional[OpenAI] = None


def get_client() -> OpenAI:
    """
    Retorna um client OpenAI singleton para o projeto Gaiden.

    Ordem de resolução da chave:
      1) .gaiden_secrets (get_openai_key)
      2) variável de ambiente OPENAI_API_KEY

    Erra alto se não encontrar.
    """
    global _client
    if _client is not None:
        return _client

    api_key = get_openai_key() or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY não encontrada. "
            "Defina em .gaiden_secrets ou no ambiente."
        )

    _client = OpenAI(api_key=api_key)
    return _client
