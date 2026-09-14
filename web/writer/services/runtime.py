from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


MIN_GPU_PERCENT = 65.0
MAX_CPU_PERCENT = 35.0
MODEL_KEEP_ALIVE = "30m"


@dataclass(frozen=True)
class AccelerationStatus:
    model: str
    gpu_percent: float
    cpu_percent: float


def _ollama_url(base_url: str, path: str) -> str:
    parsed = urlsplit(base_url)
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def require_optimal_acceleration(
    *, base_url: str, model: str
) -> AccelerationStatus:
    """Load the local model, then fail closed unless Ollama offloads it optimally."""
    load_request = Request(
        _ollama_url(base_url, "/api/generate"),
        data=json.dumps({
            "model": model,
            "prompt": "",
            "stream": False,
            "keep_alive": MODEL_KEEP_ALIVE,
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(load_request, timeout=300) as response:
            json.load(response)
        with urlopen(_ollama_url(base_url, "/api/ps"), timeout=10) as response:
            payload = json.load(response)
    except Exception as exc:
        raise RuntimeError(
            "Writer acceleration gate failed: the local Ollama runtime could not "
            "be loaded and inspected. No text generation was started."
        ) from exc

    loaded = next(
        (
            item for item in payload.get("models", [])
            if item.get("name") == model or item.get("model") == model
        ),
        None,
    )
    if loaded is None:
        raise RuntimeError(
            f"Writer acceleration gate failed: {model!r} is not loaded. "
            "No text generation was started."
        )
    size = int(loaded.get("size") or 0)
    size_vram = int(loaded.get("size_vram") or 0)
    if size <= 0 or size_vram <= 0:
        raise RuntimeError(
            "Writer acceleration gate failed: Ollama reports no active GPU "
            "offload. No text generation was started."
        )
    gpu_percent = 100.0 * size_vram / size
    cpu_percent = 100.0 - gpu_percent
    if gpu_percent < MIN_GPU_PERCENT or cpu_percent > MAX_CPU_PERCENT:
        raise RuntimeError(
            "Writer acceleration gate failed: processor distribution is "
            f"{gpu_percent:.1f}% GPU / {cpu_percent:.1f}% CPU; the canonical "
            f"minimum is {MIN_GPU_PERCENT:.0f}% GPU / maximum "
            f"{MAX_CPU_PERCENT:.0f}% CPU. No text generation was started."
        )
    return AccelerationStatus(
        model=model,
        gpu_percent=gpu_percent,
        cpu_percent=cpu_percent,
    )
