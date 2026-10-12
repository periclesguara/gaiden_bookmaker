"""Writer generation through the configured local Ollama runtime only."""
from __future__ import annotations

import os
from dataclasses import dataclass

from gaiden.infrastructure.local_models import LocalModelClient, local_model_name


WRITER_MODEL = ""
WRITER_BASE_URL = "http://127.0.0.1:11434"


def writer_model() -> str:
    return local_model_name(os.environ.get("GAIDEN_LOCAL_MODEL", ""))


@dataclass
class LocalWriterGenerator:
    supports_structured_output = True
    model: str = ""
    timeout_seconds: float = 300

    def __post_init__(self) -> None:
        self.model = local_model_name(self.model or os.environ.get("GAIDEN_LOCAL_MODEL", ""))
        if not 1 <= self.timeout_seconds <= 1800:
            raise ValueError("Writer timeout must be between 1 and 1800 seconds")
        self._client = LocalModelClient()

    def generate(self, *, system: str, user: str, max_tokens: int, temperature: float | None = None) -> str:
        if not 256 <= max_tokens <= 32768:
            raise ValueError("max_tokens must be between 256 and 32768")
        try:
            response = self._client.responses.create(
                model=self.model, instructions=system, input=user,
                max_output_tokens=max_tokens, temperature=temperature or 0.2,
            )
        except Exception:
            raise RuntimeError("Local Writer model failed; manual review required") from None
        self.last_finish_reason = "stop"
        return response.output_text

    def generate_json(self, *, system: str, user: str, max_tokens: int, schema: dict) -> str:
        if not 256 <= max_tokens <= 32768:
            raise ValueError("max_tokens must be between 256 and 32768")
        response = self._client.responses.create(
            model=self.model, instructions=system, input=user, max_output_tokens=max_tokens,
            text={"format": {"type": "json_schema", "name": "writer_response", "strict": True, "schema": schema}},
        )
        self.last_finish_reason = "stop"
        return response.output_text


def writer_generator() -> LocalWriterGenerator:
    return LocalWriterGenerator()
