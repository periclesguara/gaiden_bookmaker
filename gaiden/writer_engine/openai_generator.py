"""Writer-only OpenAI routing; local retrieval configuration stays independent."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from openai import OpenAI

from gaiden.secrets_loader import get_openai_config

WRITER_MODEL = "gpt-5.6-luna"
WRITER_BASE_URL = "https://api.openai.com/v1"


@dataclass
class OpenAIWriterGenerator:
    api_key: str = field(repr=False)
    model: str = WRITER_MODEL
    timeout_seconds: float = 180

    def __post_init__(self) -> None:
        if self.api_key.strip().casefold() in {"", "empty", "placeholder", "replace-me"}:
            raise ValueError("OPENAI_API_KEY is required for Writer generation")
        if not 1 <= self.timeout_seconds <= 1800:
            raise ValueError("Writer timeout must be between 1 and 1800 seconds")
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
                max_output_tokens=max_tokens, reasoning={"effort": "none"},
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


def writer_generator() -> OpenAIWriterGenerator:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        key = get_openai_config().get("api_key", "")
    return OpenAIWriterGenerator(api_key=key)
