from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .hardware import HardwareProfile, detect_hardware

_QUANT_RE = re.compile(r"(?:^|[-_.])((?:IQ\d(?:_[A-Z0-9]+)?)|(?:Q\d(?:_[A-Z0-9]+)+)|F16|BF16|F32)(?:[-_.]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class QuantizationPolicy:
    writer_preference: tuple[str, ...]
    embedding_preference: tuple[str, ...]
    writer_context: int
    embedding_context: int
    writer_gpu_layers: str
    embedding_gpu_layers: str
    cache_type_k: str = "q8_0"
    cache_type_v: str = "q8_0"
    fit: bool = True
    parallel: int = 1

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimePlan:
    backend: str
    hardware: HardwareProfile
    policy: QuantizationPolicy
    writer_model: Path | None
    embedding_model: Path | None
    writer_quant: str
    embedding_quant: str
    executable: str
    reason: str

    @property
    def local_ready(self) -> bool:
        return bool(self.executable and self.writer_model and self.embedding_model)

    def to_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend, "hardware": self.hardware.to_dict(),
            "policy": self.policy.to_dict(),
            "writer_model": str(self.writer_model) if self.writer_model else "",
            "embedding_model": str(self.embedding_model) if self.embedding_model else "",
            "writer_quant": self.writer_quant, "embedding_quant": self.embedding_quant,
            "executable": self.executable, "local_ready": self.local_ready, "reason": self.reason,
        }


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().casefold() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def policy_for_hardware(profile: HardwareProfile | None = None) -> QuantizationPolicy:
    profile = profile or detect_hardware()
    if profile.has_gpu and profile.gpu_vram_gb >= 12:
        writer_pref, writer_ctx, embedding_gpu = ("Q5_K_M", "Q4_K_M", "Q6_K", "Q8_0", "Q3_K_M"), 12288, "auto"
    elif profile.has_gpu and profile.gpu_vram_gb >= 6:
        writer_pref, writer_ctx, embedding_gpu = ("Q4_K_M", "Q5_K_M", "Q3_K_M", "Q6_K", "Q8_0"), 8192, "0" if profile.gpu_vram_gb < 10 else "auto"
    elif profile.ram_gb and profile.ram_gb < 12:
        writer_pref, writer_ctx, embedding_gpu = ("Q3_K_M", "Q4_K_M", "Q2_K", "Q5_K_M"), 4096, "0"
    else:
        writer_pref, writer_ctx, embedding_gpu = ("Q4_K_M", "Q5_K_M", "Q3_K_M", "Q6_K", "Q8_0"), 6144, "0"
    return QuantizationPolicy(
        writer_preference=writer_pref,
        embedding_preference=("Q8_0", "F16", "BF16", "Q6_K", "Q5_K_M", "Q4_K_M"),
        writer_context=_env_int("GAIDEN_RUNTIME_CONTEXT", writer_ctx, 2048),
        embedding_context=_env_int("GAIDEN_RUNTIME_EMBEDDING_CONTEXT", 8192, 2048),
        writer_gpu_layers=os.environ.get("GAIDEN_RUNTIME_GPU_LAYERS", "auto").strip() or "auto",
        embedding_gpu_layers=os.environ.get("GAIDEN_RUNTIME_EMBEDDING_GPU_LAYERS", embedding_gpu).strip() or embedding_gpu,
        cache_type_k=os.environ.get("GAIDEN_RUNTIME_CACHE_TYPE_K", "q8_0").strip() or "q8_0",
        cache_type_v=os.environ.get("GAIDEN_RUNTIME_CACHE_TYPE_V", "q8_0").strip() or "q8_0",
        fit=_env_bool("GAIDEN_RUNTIME_FIT", True),
        parallel=_env_int("GAIDEN_RUNTIME_PARALLEL", 1, 1),
    )


def quant_from_filename(path: Path | None) -> str:
    match = _QUANT_RE.search(path.name) if path else None
    return match.group(1).upper() if match else ("" if not path else "UNKNOWN")


def _model_candidates(root: Path, role: str) -> list[Path]:
    candidates, seen = [], set()
    for directory in (root / role, root):
        if not directory.exists():
            continue
        for path in directory.rglob("*.gguf"):
            resolved = path.resolve()
            if resolved in seen or path.name.casefold().startswith("mmproj") or re.search(r"-\d{5}-of-\d{5}\.gguf$", path.name, re.IGNORECASE):
                continue
            seen.add(resolved)
            candidates.append(resolved)
    return candidates


def _select(candidates: list[Path], preference: tuple[str, ...], role: str) -> Path | None:
    if not candidates:
        return None
    def score(path: Path) -> tuple[int, int, int, str]:
        try:
            rank = preference.index(quant_from_filename(path))
        except ValueError:
            rank = len(preference) + 1
        is_embedding = any(word in path.name.casefold() for word in ("embed", "bge", "e5", "nomic"))
        return (0 if is_embedding == (role == "embedding") else 2, rank, path.stat().st_size, path.name.upper())
    return min(candidates, key=score)


def _explicit_path(name: str, root: Path) -> Path | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path if path.exists() and path.suffix.casefold() == ".gguf" else None


def plan_runtime(*, model_root: Path | None = None, executable: str = "", profile: HardwareProfile | None = None) -> RuntimePlan:
    profile = profile or detect_hardware()
    policy = policy_for_hardware(profile)
    root = (model_root or Path(os.environ.get("GAIDEN_MODEL_ROOT", ".runtime/models"))).expanduser()
    root = (Path.cwd() / root).resolve() if not root.is_absolute() else root.resolve()
    writer = _explicit_path("GAIDEN_WRITER_GGUF", root) or _select(_model_candidates(root, "writer"), policy.writer_preference, "writer")
    embedding = _explicit_path("GAIDEN_EMBEDDING_GGUF", root) or _select(_model_candidates(root, "embedding"), policy.embedding_preference, "embedding")
    requested = os.environ.get("GAIDEN_RUNTIME_BACKEND", "auto").strip().casefold()
    requested = requested if requested in {"auto", "llamacpp", "openai"} else "auto"
    ready = bool(executable and writer and embedding)
    if requested == "llamacpp":
        backend, reason = "llamacpp", "local llama.cpp explicitly requested"
    elif requested == "openai":
        backend, reason = "openai", "OpenAI-compatible endpoints explicitly requested"
    elif ready:
        backend, reason = "llamacpp", "auto selected local GGUF because llama.cpp and both models are available"
    else:
        missing = [label for present, label in ((bool(executable), "llama.cpp executable"), (bool(writer), "writer GGUF"), (bool(embedding), "embedding GGUF")) if not present]
        backend, reason = "openai", "auto fell back to configured OpenAI-compatible endpoints; missing " + ", ".join(missing)
    return RuntimePlan(backend, profile, policy, writer, embedding, quant_from_filename(writer), quant_from_filename(embedding), executable, reason)
