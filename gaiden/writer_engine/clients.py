from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from gaiden.infrastructure.local_models import LocalModelClient, local_model_name, validate_local_model_name


class Embedder(Protocol):
    model: str
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class Generator(Protocol):
    model: str
    def generate(self, *, system: str, user: str, max_tokens: int, temperature: float | None = None) -> str: ...


@dataclass
class LocalChatGenerator:
    """Bounded generation using the operator's loopback Ollama model."""
    base_url: str = "http://127.0.0.1:11434"
    model: str = ""
    temperature: float = 0.1
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        from gaiden.infrastructure.local_models import _local_base_url
        _local_base_url()
        self.model = local_model_name(self.model)
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be at least 1")
        self._client = LocalModelClient()

    def generate(self, *, system: str, user: str, max_tokens: int, temperature: float | None = None) -> str:
        if not 256 <= max_tokens <= 32768:
            raise ValueError("max_tokens must be between 256 and 32768")
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens, temperature=self.temperature if temperature is None else temperature,
        )
        self.last_finish_reason = response.choices[0].finish_reason
        return (response.choices[0].message.content or "").strip()


@dataclass
class LocalEmbeddingClient:
    model: str = ""
    batch_size: int = 16

    def __post_init__(self) -> None:
        import os
        self.model = validate_local_model_name(
            os.environ.get("GAIDEN_LOCAL_EMBEDDING_MODEL") or self.model,
            "GAIDEN_LOCAL_EMBEDDING_MODEL",
        )
        if not 1 <= self.batch_size <= 128:
            raise ValueError("embedding batch_size must be between 1 and 128")
        self._client = LocalModelClient()

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            result = self._client.embeddings.create(model=self.model, input=texts[start:start + self.batch_size])
            ordered = sorted(result.data, key=lambda item: item.index)
            vectors.extend([list(item.embedding) for item in ordered])
        if len(vectors) != len(texts):
            raise RuntimeError("local embedding endpoint returned an incomplete batch")
        return vectors
