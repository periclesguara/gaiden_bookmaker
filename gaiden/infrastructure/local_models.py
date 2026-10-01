"""Loopback-only Ollama client shared by all model-backed Gaiden stages.

This module intentionally has no cloud-provider SDK or remote fallback. Model
names are selected by the operator, and the Qwen/Phi families are reserved.
"""
from __future__ import annotations

import ipaddress
import json
import os
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


def _local_base_url() -> str:
    value = os.environ.get("GAIDEN_LOCAL_MODEL_URL", "http://127.0.0.1:11434").strip()
    parsed = urlsplit(value)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("GAIDEN_LOCAL_MODEL_URL must be an unauthenticated loopback HTTP URL")
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = parsed.hostname.casefold() == "localhost"
    if not loopback:
        raise ValueError("External model endpoints are disabled; use loopback Ollama")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def validate_local_model_name(value: str | None, setting: str = "GAIDEN_LOCAL_MODEL") -> str:
    model = (value or "").strip()
    if not model:
        raise RuntimeError(f"Configure {setting} with an installed local Ollama model")
    lowered = model.casefold()
    if "qwen" in lowered or "phi" in lowered or "gpt" in lowered or "openai" in lowered:
        raise ValueError(f"{setting} must not select Qwen, Phi, or a cloud model")
    return model


def local_model_name(requested: str | None = None) -> str:
    return validate_local_model_name(os.environ.get("GAIDEN_LOCAL_MODEL") or requested)


def _post(path: str, payload: dict, timeout: float = 300) -> dict:
    base = _local_base_url()
    request = Request(
        f"{base}/api/{path.lstrip('/')}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except Exception:
        raise RuntimeError("Local model service unavailable; no external model was contacted") from None
    if not isinstance(result, dict):
        raise RuntimeError("Local model service returned an invalid response")
    return result


def _message_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in value)
    return str(value or "")


class LocalModelClient:
    def __init__(self):
        _local_base_url()
        self.responses = _Responses(self)
        self.chat = SimpleNamespace(completions=_ChatCompletions(self))
        self.embeddings = _Embeddings(self)

    def generate(self, *, model: str | None, messages: list[dict], max_tokens: int = 4096,
                 temperature: float = 0.2, schema: dict | None = None, tools=None) -> dict:
        local_model = local_model_name()
        normalized = [{"role": m.get("role", "user"), "content": _message_text(m.get("content"))} for m in messages]
        payload = {
            "model": local_model,
            "messages": normalized,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }
        if schema:
            payload["format"] = schema
        if tools:
            payload["tools"] = tools
        result = _post("chat", payload, timeout=float(os.environ.get("GAIDEN_LOCAL_MODEL_TIMEOUT", "300")))
        message = result.get("message") or {}
        content = str(message.get("content") or "").strip()
        done = bool(result.get("done", True))
        if not done or not content:
            raise RuntimeError("Local model returned an empty or incomplete response")
        return {"model": local_model, "content": content, "done_reason": result.get("done_reason", "stop"),
                "prompt_eval_count": result.get("prompt_eval_count"), "eval_count": result.get("eval_count"),
                "tool_calls": message.get("tool_calls", [])}


class _Responses:
    def __init__(self, client): self.client = client

    def create(self, *, input, model=None, instructions=None, max_output_tokens=4096,
               temperature=0.2, text=None, tools=None, **_ignored):
        messages = input if isinstance(input, list) else [{"role": "user", "content": input}]
        if instructions:
            messages = [{"role": "system", "content": instructions}, *messages]
        schema = None
        if isinstance(text, dict):
            fmt = text.get("format") or {}
            if fmt.get("type") in {"json_schema", "json"}:
                schema = fmt.get("schema") or {"type": "object"}
        result = self.client.generate(model=model, messages=messages, max_tokens=max_output_tokens,
                                      temperature=temperature, schema=schema, tools=tools)
        content = result["content"]
        usage = SimpleNamespace(input_tokens=result["prompt_eval_count"], output_tokens=result["eval_count"])
        return SimpleNamespace(status="completed", output_text=content, model=result["model"], usage=usage,
                               output=[SimpleNamespace(content=[SimpleNamespace(text=content)])])


class _ChatCompletions:
    def __init__(self, client): self.client = client

    def create(self, *, messages, model=None, max_tokens=4096, temperature=0.2, **kwargs):
        result = self.client.generate(model=model, messages=messages, max_tokens=max_tokens,
                                      temperature=temperature, tools=kwargs.get("tools"))
        message = SimpleNamespace(content=result["content"], tool_calls=result["tool_calls"] or None)
        choice = SimpleNamespace(message=message, finish_reason=result["done_reason"])
        usage = SimpleNamespace(prompt_tokens=result["prompt_eval_count"], completion_tokens=result["eval_count"])
        return SimpleNamespace(model=result["model"], choices=[choice], usage=usage)


class _Embeddings:
    def __init__(self, client): self.client = client

    def create(self, *, input, model=None, **_kwargs):
        local_model = validate_local_model_name(
            os.environ.get("GAIDEN_LOCAL_EMBEDDING_MODEL"), "GAIDEN_LOCAL_EMBEDDING_MODEL"
        )
        result = _post("embed", {"model": local_model, "input": input},
                       timeout=float(os.environ.get("GAIDEN_LOCAL_MODEL_TIMEOUT", "300")))
        vectors = result.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(input):
            raise RuntimeError("Local embedding service returned an incomplete batch")
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=vector) for i, vector in enumerate(vectors)],
                               model=local_model, usage=None)
