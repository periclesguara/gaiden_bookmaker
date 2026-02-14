from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from openai import OpenAI

# -------------------------------------------------------------------
# OpenAI client
# -------------------------------------------------------------------

_client: Optional[OpenAI] = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        # respeita OPENAI_API_KEY e OPENAI_BASE_URL se você já usa isso
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE") or "https://api.openai.com/v1"
        _client = OpenAI(api_key=api_key, base_url=base_url)
        # log mínimo (você já tem algo parecido)
        if os.getenv("GAIDEN_DEBUG_OUTPUT") == "1":
            print(
                f"[OPENAI] api_key_len={len(api_key or '')} base_url={base_url} "
                f"default_model={os.getenv('GAIDEN_DEFAULT_MODEL','')}"
            )
    return _client


# -------------------------------------------------------------------
# Agent prompt loading
# -------------------------------------------------------------------


def _agent_env_key(agent_name: str) -> str:
    # ALAMAGUEDERAZ -> GAIDEN_AGENT_ALAMAGUEDERAZ_SYSTEM_PROMPT
    safe = (agent_name or "").strip().upper()
    safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in safe)
    return f"GAIDEN_AGENT_{safe}_SYSTEM_PROMPT"


def _load_agent_system_prompt(agent_name: str) -> str:
    # 1) ENV wins
    env_key = _agent_env_key(agent_name)
    env_val = os.getenv(env_key)
    if env_val and env_val.strip():
        return env_val.strip()

    # 2) file fallback
    # place prompts here: gaiden/agents/ALAMAGUEDERAZ.txt etc.
    prompt_path = Path("gaiden") / "agents" / f"{agent_name}.txt"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8", errors="strict").strip()

    # 3) hard fail (não deixa rodar "modelo cru" e destruir chunk)
    raise RuntimeError(
        f"AGENT_PROMPT_MISSING: {agent_name}. "
        f"Set {env_key} or create {prompt_path}"
    )


# -------------------------------------------------------------------
# Core call: agent text
# -------------------------------------------------------------------


def call_agent_text(
    *,
    agent_name: str,
    text: str,
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_output_tokens: int = 8000,
) -> str:
    """
    Calls a named "agent" by injecting its system prompt via Responses API.

    IMPORTANT:
    - This is NOT the Dashboard Agent Builder by ID.
    - It's a lightweight "agent-by-prompt" approach: agent_name -> system prompt.
    """

    client = get_client()

    # model default for agent flow
    model = (
        model
        or os.getenv("GAIDEN_AGENT_DEFAULT_MODEL")
        or os.getenv("GAIDEN_DEFAULT_MODEL")
        or "gpt-5-chat-latest"
    )

    system_prompt = _load_agent_system_prompt(agent_name)

    # Responses API: instructions (system) + input (user text)
    resp = client.responses.create(
        model=model,
        instructions=system_prompt,
        input=text,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        # store=False,  # se você quiser não armazenar
    )

    out = (resp.output_text or "").strip("\n")
    return out
