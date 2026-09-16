from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

from openai import OpenAI


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class Generator(Protocol):
    model: str

    def generate(self, *, system: str, user: str, max_tokens: int, temperature: float | None = None) -> str: ...


@dataclass
class LocalChatGenerator:
    """Bounded client for a local OpenAI-compatible chat model."""

    base_url: str
    model: str
    api_key: str = "EMPTY"
    temperature: float = 0.1
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        _validate_endpoint(self.base_url, self.api_key)
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be at least 1")
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            max_retries=0,
            timeout=self.timeout_seconds,
        )

    def generate(self, *, system: str, user: str, max_tokens: int,
                 temperature: float | None = None) -> str:
        if not 256 <= max_tokens <= 32768:
            raise ValueError("max_tokens must be between 256 and 32768")
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=self.temperature if temperature is None else temperature,
        )
        choice = response.choices[0]
        self.last_finish_reason = getattr(choice, "finish_reason", "stop")
        return (choice.message.content or "").strip()


def _validate_endpoint(base_url: str, api_key: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("model endpoint must be an http(s) URL")
    try:
        is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        is_loopback = parsed.hostname.casefold() == "localhost"
    if not is_loopback and api_key.strip().casefold() in {
        "", "empty", "placeholder", "replace-me"
    }:
        raise ValueError("a real API key is required for non-loopback model endpoints")


@dataclass
class OpenAIEmbeddingClient:
    base_url: str
    api_key: str = "EMPTY"
    model: str = "text-embedding-3-small"
    batch_size: int = 16

    def __post_init__(self) -> None:
        _validate_endpoint(self.base_url, self.api_key)
        if not 1 <= self.batch_size <= 128:
            raise ValueError("embedding batch_size must be between 1 and 128")
        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key, max_retries=0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = self._client.embeddings.create(
                model=self.model,
                input=batch,
                encoding_format="float",
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend([list(item.embedding) for item in ordered])
        if len(vectors) != len(texts):
            raise RuntimeError("embedding endpoint returned an incomplete batch")
        return vectors
