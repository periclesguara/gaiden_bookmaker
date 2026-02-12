from __future__ import annotations

import os
from typing import Optional

from openai import OpenAI
from gaiden.secrets_loader import bootstrap_openai_env

_client: Optional[OpenAI] = None


def choose_model(
    *,
    stage: str | None,
    contract_model: str | None,
    env_default: str | None,
) -> str:
    stage_norm = (stage or "").strip().lower()
    if stage_norm == "translate":
        if not contract_model:
            raise RuntimeError(
                "TRANSLATE MODEL VIOLATION: stage=translate requires model=gpt-5.2 (contract missing model)"
            )
        if contract_model != "gpt-5.2":
            raise RuntimeError(
                f"TRANSLATE MODEL VIOLATION: stage=translate requires model=gpt-5.2 (contract says {contract_model})"
            )
        return contract_model

    if contract_model:
        return contract_model

    return env_default or "gpt-5-chat-latest"


def get_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    dry_run: bool = False,
) -> OpenAI:
    """
    Retorna um client OpenAI singleton para o projeto Gaiden.
    """
    global _client
    if _client is not None:
        return _client

    cfg = None
    if api_key is None or base_url is None:
        cfg = bootstrap_openai_env(dry_run=dry_run)

    if api_key is None:
        api_key = (
            (cfg or {}).get("api_key")
            or (cfg or {}).get("OPENAI_API_KEY")
            or (cfg or {}).get("openai_api_key")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        ).strip()

    if not api_key and not dry_run:
        raise RuntimeError(
            "OpenAI API key não encontrada: verifique .gaiden_secrets (OPENAI_API_KEY=...)"
        )
    if not api_key and dry_run:
        api_key = "DUMMY_DRY_RUN_KEY"

    if base_url is None:
        base_url = (
            (cfg or {}).get("base_url")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        )

    default_model = (cfg or {}).get("default_model") or "gpt-5-chat-latest"

    print(f"[OPENAI] api_key_len={len(api_key)} base_url={base_url} default_model={default_model}")

    # Ensure process env is populated for downstream libs.
    os.environ["OPENAI_API_KEY"] = api_key
    if base_url:
        os.environ["OPENAI_BASE_URL"] = base_url
    for k in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"]:
        if (cfg or {}).get(k):
            os.environ[k] = (cfg or {})[k]

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
    cfg = bootstrap_openai_env(dry_run=False)
    client = get_client(
        api_key=cfg.get("api_key") or cfg.get("OPENAI_API_KEY") or None,
        base_url=cfg.get("base_url") or cfg.get("OPENAI_BASE_URL") or None,
        dry_run=False,
    )

    env_key = f"GAIDEN_AGENT_{agent_name.upper()}_MODEL"
    contract_model = (
        model
        or os.environ.get(env_key)
        or (agent_name if agent_name.startswith("gpt-") else None)
    )
    env_default = os.environ.get("GAIDEN_DEFAULT_MODEL") or cfg.get("default_model") or "gpt-5-chat-latest"
    resolved_model = choose_model(stage="agent", contract_model=contract_model, env_default=env_default)

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
