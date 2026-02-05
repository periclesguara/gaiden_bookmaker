from __future__ import annotations

import os
from typing import Optional

from openai import OpenAI
from gaiden.secrets_loader import get_openai_config

_client: Optional[OpenAI] = None


def get_client() -> OpenAI:
    """
    Retorna um client OpenAI singleton para o projeto Gaiden.
    """
    global _client
    if _client is not None:
        return _client

    cfg = get_openai_config()

    api_key = (
        cfg.get("api_key")
        or cfg.get("OPENAI_API_KEY")
        or cfg.get("openai_api_key")
        or ""
    ).strip()

    if not api_key:
        raise RuntimeError(
            "OpenAI API key não encontrada: verifique .gaiden_secrets (OPENAI_API_KEY=...)"
        )

    base_url = cfg.get("base_url") or cfg.get("OPENAI_BASE_URL") or None
    if base_url:
        base_url = base_url.strip().rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
    default_model = cfg.get("default_model") or "gpt5-chat-latest"

    print(
        f"[OPENAI] api_key_len={len(api_key)} base_url={base_url} default_model={default_model}"
    )

    # Ensure process env is populated for downstream libs.
    os.environ["OPENAI_API_KEY"] = api_key
    if base_url:
        os.environ["OPENAI_BASE_URL"] = base_url
    if default_model:
        os.environ["GAIDEN_DEFAULT_MODEL"] = default_model
    for k in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"]:
        if cfg.get(k):
            os.environ[k] = cfg[k]

    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    _client = OpenAI(**client_kwargs)
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
