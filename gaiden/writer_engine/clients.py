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

    def generate(self, *, system: str, user: str, max_tokens: int) -> str: ...


def _validate_endpoint(base_url: str, api_key: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("model endpoint must be an http(s) URL")
    try:
        is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        is_loopback = parsed.hostname.casefold() == "localhost"
    if not is_loopback and api_key.strip().casefold() in {"", "empty", "replace-me"}:
        raise ValueError("a real API key is required for non-loopback model endpoints")


@dataclass
class OpenAIEmbeddingClient:
    base_url: str
    api_key: str = "EMPTY"
    model: str = "Qwen/Qwen3-Embedding-0.6B"
    batch_size: int = 16

    def __post_init__(self) -> None:
        _validate_endpoint(self.base_url, self.api_key)
        if not 1 <= self.batch_size <= 128:
            raise ValueError("embedding batch_size must be between 1 and 128")
        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)

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


@dataclass
class QwenGenerator:
    base_url: str
    api_key: str = "EMPTY"
    model: str = "Qwen/Qwen3.5-9B"
    temperature: float = 0.7
    top_p: float = 0.8
    thinking: bool = False

    def __post_init__(self) -> None:
        _validate_endpoint(self.base_url, self.api_key)
        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)

    def generate(self, *, system: str, user: str, max_tokens: int) -> str:
        if not 256 <= max_tokens <= 32768:
            raise ValueError("max_tokens must be between 256 and 32768")
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=self.temperature,
            top_p=self.top_p,
            presence_penalty=1.5,
            extra_body={
                "top_k": 20,
                "chat_template_kwargs": {"enable_thinking": self.thinking},
            },
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise RuntimeError("Qwen returned an empty draft")
        return content.strip()
