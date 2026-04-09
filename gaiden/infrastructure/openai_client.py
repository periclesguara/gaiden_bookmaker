from __future__ import annotations

import os
from typing import Any, Optional

from openai import OpenAI

from .env import require_openai_api_key

_client: Optional[OpenAI] = None


def get_client() -> OpenAI:
    global _client
    if _client is not None:
        return _client
    _client = OpenAI(api_key=require_openai_api_key())
    return _client


def choose_model(*, stage: str | None = None, contract_model: str | None = None, env_default: str | None = None) -> str:
    if contract_model and str(contract_model).strip():
        return str(contract_model).strip()
    stage_key = (stage or "default").strip().upper()
    env_specific = os.environ.get(f"GAIDEN_MODEL_{stage_key}", "").strip()
    if env_specific:
        return env_specific
    if env_default and str(env_default).strip():
        return str(env_default).strip()
    return os.environ.get("GAIDEN_DEFAULT_MODEL", "gpt-5.4").strip() or "gpt-5.4"


def openai_healthcheck() -> tuple[bool, str]:
    try:
        require_openai_api_key()
    except Exception as exc:
        return False, str(exc)
    try:
        get_client()
    except Exception as exc:
        return False, f"OpenAI client bootstrap failed: {exc}"
    return True, "ok"


def call_agent_text(
    *,
    agent_name: str,
    text: str,
    model: str | None = None,
    temperature: float = 0.4,
    max_output_tokens: int = 8000,
    system_prompt: str | None = None,
) -> str:
    client = get_client()
    prompt = system_prompt or f"You are agent {agent_name}. Return only the transformed text."
    response = client.responses.create(
        model=choose_model(stage=agent_name, contract_model=model, env_default=None),
        input=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )
    output_text = getattr(response, "output_text", "") or ""
    if output_text.strip():
        return output_text.strip()
    try:
        return response.output[0].content[0].text.strip()
    except Exception as exc:
        raise RuntimeError(f"Agent {agent_name} returned no output text.") from exc
