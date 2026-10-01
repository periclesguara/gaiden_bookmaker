"""Compatibility import path backed exclusively by a loopback Ollama service."""
from __future__ import annotations

import os
from typing import Any

from .local_models import LocalModelClient, local_model_name

_client: LocalModelClient | None = None


def get_client() -> LocalModelClient:
    global _client
    if _client is None:
        _client = LocalModelClient()
    return _client


def choose_model(*, stage: str | None = None, contract_model: str | None = None, env_default: str | None = None) -> str:
    return local_model_name()


def openai_healthcheck() -> tuple[bool, str]:
    return False, "External OpenAI access is disabled; configure the local Ollama runtime."


def call_agent_text(*, agent_name: str, text: str, model: str | None = None,
                    temperature: float = 0.4, max_output_tokens: int = 8000,
                    system_prompt: str | None = None) -> str:
    response = get_client().responses.create(
        model=choose_model(stage=agent_name, contract_model=model),
        input=[{"role": "system", "content": system_prompt or f"You are agent {agent_name}. Return only the transformed text."},
               {"role": "user", "content": text}],
        temperature=temperature, max_output_tokens=max_output_tokens,
    )
    return response.output_text
