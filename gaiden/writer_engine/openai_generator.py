"""Writer-only OpenAI routing; local retrieval configuration stays independent."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from openai import OpenAI

from gaiden.infrastructure.env import get_openai_api_key

WRITER_MODEL = "gpt-5.6-luna"
WRITER_BASE_URL = "https://api.openai.com/v1"
WRITER_REASONING_EFFORT = "medium"
_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})


def writer_reasoning_effort() -> str:
    """Return the bounded Writer reasoning setting from local operator config."""
    effort = os.environ.get(
        "GAIDEN_WRITER_REASONING_EFFORT", WRITER_REASONING_EFFORT
    ).strip().casefold()
    if effort not in _REASONING_EFFORTS:
        raise ValueError("GAIDEN_WRITER_REASONING_EFFORT is invalid")
    return effort


@dataclass
class OpenAIWriterGenerator:
    supports_structured_output = True
    api_key: str = field(repr=False)
    model: str = WRITER_MODEL
    reasoning_effort: str = WRITER_REASONING_EFFORT
    timeout_seconds: float = 180

    def __post_init__(self) -> None:
        if self.api_key.strip().casefold() in {"", "empty", "placeholder", "replace-me"}:
            raise ValueError("OPENAI_API_KEY is required for Writer generation")
        if not 1 <= self.timeout_seconds <= 1800:
            raise ValueError("Writer timeout must be between 1 and 1800 seconds")
        if self.reasoning_effort not in _REASONING_EFFORTS:
            raise ValueError("Writer reasoning effort is invalid")
        self._client = OpenAI(
            api_key=self.api_key, base_url=WRITER_BASE_URL,
            max_retries=0, timeout=self.timeout_seconds,
        )

    def generate(self, *, system: str, user: str, max_tokens: int,
                 temperature: float | None = None) -> str:
        # Accept the engine/critic interface; do not send local sampling knobs.
        if not 256 <= max_tokens <= 32768:
            raise ValueError("max_tokens must be between 256 and 32768")
        self.last_finish_reason = "error"
        try:
            response = self._client.responses.create(
                model=self.model, instructions=system, input=user,
                max_output_tokens=max_tokens, reasoning={"effort": self.reasoning_effort},
                store=False,
            )
        except Exception:
            # Django records exception text in session errors: never copy API bodies.
            raise RuntimeError("OpenAI Writer request failed; manual review required") from None
        content = (response.output_text or "").strip()
        if response.status != "completed" or not content:
            self.last_finish_reason = "length" if response.status == "incomplete" else "error"
            raise RuntimeError("OpenAI Writer returned empty or incomplete output")
        self.last_finish_reason = "stop"
        return content

    def generate_json(self, *, system: str, user: str, max_tokens: int, schema: dict) -> str:
        """Generate a structured critic response without relaxing Writer safety checks."""
        if not 256 <= max_tokens <= 32768:
            raise ValueError("max_tokens must be between 256 and 32768")
        self.last_finish_reason = "error"
        try:
            response = self._client.responses.create(
                model=self.model,
                instructions=system,
                input=user,
                max_output_tokens=max_tokens,
                reasoning={"effort": self.reasoning_effort},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "writer_critic_response",
                        "strict": True,
                        "schema": schema,
                    }
                },
                store=False,
            )
        except Exception:
            raise RuntimeError("OpenAI Writer request failed; manual review required") from None
        content = (response.output_text or "").strip()
        if response.status != "completed" or not content:
            self.last_finish_reason = "length" if response.status == "incomplete" else "error"
            raise RuntimeError("OpenAI Writer returned empty or incomplete output")
        self.last_finish_reason = "stop"
        return content


def writer_generator() -> OpenAIWriterGenerator:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        key = get_openai_api_key() or ""
    return OpenAIWriterGenerator(
        api_key=key, reasoning_effort=writer_reasoning_effort()
    )
