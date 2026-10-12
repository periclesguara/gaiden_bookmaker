from __future__ import annotations

"""Compatibility wrapper. New code should import gaiden.infrastructure.openai_client."""

from gaiden.infrastructure.openai_client import (
    call_agent_text,
    choose_model,
    get_client,
    openai_healthcheck,
)
