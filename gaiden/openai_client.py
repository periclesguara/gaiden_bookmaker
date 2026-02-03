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


def _extract_output_text(resp) -> str:
    """
    Extract text from Responses API in a defensive way.
    """
    try:
        return resp.output[0].content[0].text
    except Exception:
        pass
    try:
        return resp.output_text
    except Exception:
        pass
    raise RuntimeError("Could not extract text from model response.")


def call_agent_text(
    agent_name: str,
    text: str,
    *,
    system_prompt: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
) -> str:
    """
    Send text to a named agent/model and return the raw text response.
    If agent_name is not a model id, resolve via env or fallback.
    """
    client = get_client()

    env_key = f"GAIDEN_AGENT_{agent_name.upper()}_MODEL"
    resolved_model = (
        model
        or os.environ.get(env_key)
        or (agent_name if agent_name.startswith("gpt-") else None)
        or os.environ.get("GAIDEN_DEFAULT_MODEL", "gpt-5.1")
    )

    env_sys_key = f"GAIDEN_AGENT_{agent_name.upper()}_SYSTEM_PROMPT"
    resolved_system = system_prompt or os.environ.get(env_sys_key, "")

    messages = []
    if resolved_system.strip():
        messages.append({"role": "system", "content": resolved_system.strip()})
    messages.append({"role": "user", "content": text})

    kwargs = {}
    if temperature is not None:
        kwargs["temperature"] = float(temperature)
    if max_output_tokens is not None:
        kwargs["max_output_tokens"] = int(max_output_tokens)

    resp = client.responses.create(
        model=resolved_model,
        input=messages,
        **kwargs,
    )

    return _extract_output_text(resp).strip()
