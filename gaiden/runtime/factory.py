from __future__ import annotations

import os
import shutil
from pathlib import Path

from gaiden.writer_engine.clients import OpenAIEmbeddingClient, QwenGenerator

from .hardware import detect_hardware
from .manager import find_llama_executable
from .policy import plan_runtime


def build_embedder() -> OpenAIEmbeddingClient:
    return OpenAIEmbeddingClient(
        base_url=os.environ.get("GAIDEN_EMBEDDING_BASE_URL", "http://127.0.0.1:8001/v1"),
        api_key=os.environ.get("GAIDEN_EMBEDDING_API_KEY", "placeholder"),
        model=os.environ.get("GAIDEN_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"),
        batch_size=max(1, int(os.environ.get("GAIDEN_EMBEDDING_BATCH_SIZE", "16"))),
    )


def build_generator(*, temperature: float = 0.7) -> QwenGenerator:
    return QwenGenerator(
        base_url=os.environ.get("GAIDEN_QWEN_BASE_URL", "http://127.0.0.1:8000/v1"),
        api_key=os.environ.get("GAIDEN_QWEN_API_KEY", "placeholder"),
        model=os.environ.get("GAIDEN_QWEN_MODEL", "Qwen/Qwen3.5-9B"),
        temperature=temperature,
        top_p=float(os.environ.get("GAIDEN_QWEN_TOP_P", "0.8")),
        thinking=os.environ.get("GAIDEN_QWEN_THINKING", "0").casefold() in {"1", "true", "yes", "on"},
    )


def runtime_summary(*, repo_root: Path | None = None) -> dict[str, object]:
    executable = find_llama_executable()
    root = repo_root or Path.cwd()
    model_root = Path(os.environ.get("GAIDEN_MODEL_ROOT", ".runtime/models")).expanduser()
    if not model_root.is_absolute():
        model_root = root / model_root
    plan = plan_runtime(model_root=model_root, executable=executable.path if executable else "", profile=detect_hardware())
    summary = plan.to_dict()
    summary.update({
        "llama_mode": executable.mode if executable else "", "llama_in_path": bool(executable),
        "nvidia_smi": bool(shutil.which("nvidia-smi")), "active": os.environ.get("GAIDEN_RUNTIME_ACTIVE", "external"),
        "qwen_base_url": os.environ.get("GAIDEN_QWEN_BASE_URL", ""),
        "embedding_base_url": os.environ.get("GAIDEN_EMBEDDING_BASE_URL", ""),
    })
    return summary
