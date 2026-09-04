from __future__ import annotations

import atexit
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .policy import RuntimePlan


@dataclass(frozen=True)
class LlamaExecutable:
    path: str
    mode: str


def find_llama_executable() -> LlamaExecutable | None:
    configured = os.environ.get("GAIDEN_LLAMA_SERVER_BIN", "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        if path.is_file():
            return LlamaExecutable(str(path), "cli" if path.stem.casefold() == "llama" else "server")
    for candidate, mode in (("llama-server", "server"), ("llama-server.exe", "server"), ("llama", "cli"), ("llama.exe", "cli")):
        resolved = shutil.which(candidate)
        if resolved:
            return LlamaExecutable(resolved, mode)
    return None


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _health_ready(base_url: str) -> bool:
    url = base_url.removesuffix("/v1").rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=0.8) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


class ManagedLlamaServer:
    def __init__(self, *, executable: LlamaExecutable, model: Path, alias: str, port: int, context: int, gpu_layers: str, fit: bool, parallel: int, log_path: Path, cache_type_k: str = "q8_0", cache_type_v: str = "q8_0", embedding: bool = False, embedding_pooling: str = "last", reasoning: str = "auto") -> None:
        if not 1 <= port <= 65535:
            raise ValueError("llama.cpp port must be between 1 and 65535")
        self.executable, self.model, self.alias, self.port = executable, model, alias, port
        self.context, self.gpu_layers, self.fit, self.parallel = context, gpu_layers, fit, parallel
        self.log_path, self.cache_type_k, self.cache_type_v = log_path, cache_type_k, cache_type_v
        self.embedding, self.embedding_pooling, self.reasoning = embedding, embedding_pooling, reasoning
        self.process: subprocess.Popen[bytes] | None = None
        self._log_handle = None
        self.owned = False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def command(self) -> list[str]:
        cmd = [self.executable.path]
        if self.executable.mode == "cli":
            cmd.append("serve")
        cmd.extend(["-m", str(self.model), "--alias", self.alias, "--host", "127.0.0.1", "--port", str(self.port), "-c", str(self.context), "-ngl", self.gpu_layers, "-np", str(self.parallel), "-fit", "on" if self.fit else "off", "-ctk", self.cache_type_k, "-ctv", self.cache_type_v, "--no-webui"])
        if self.embedding:
            cmd.extend(["--embedding", "--pooling", self.embedding_pooling])
        elif self.reasoning in {"on", "off", "auto"}:
            cmd.extend(["--reasoning", self.reasoning])
        return cmd

    def start(self, timeout: float = 90.0) -> None:
        if _port_open("127.0.0.1", self.port):
            if _health_ready(self.base_url):
                return
            raise RuntimeError(f"port {self.port} is already in use by a non-ready service")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = self.log_path.open("ab", buffering=0)
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        self.process = subprocess.Popen(self.command(), stdout=self._log_handle, stderr=subprocess.STDOUT, cwd=self.model.parent, creationflags=creationflags, start_new_session=os.name != "nt")
        self.owned = True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"llama.cpp exited while loading {self.alias}; see {self.log_path}")
            if _health_ready(self.base_url):
                return
            time.sleep(0.35)
        self.stop()
        raise TimeoutError(f"timed out loading {self.alias}; see {self.log_path}")

    def stop(self) -> None:
        if self.process is not None and self.owned and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.process = None
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


class QuantizedRuntimeAdapter:
    """Starts role-specific GGUF llama.cpp servers on the local loopback interface."""

    def __init__(self, plan: RuntimePlan, *, runtime_root: Path) -> None:
        if plan.backend != "llamacpp" or not plan.local_ready:
            raise ValueError("local llama.cpp plan requires executable plus writer and embedding GGUFs")
        executable_path = Path(plan.executable)
        executable = LlamaExecutable(str(executable_path), "cli" if executable_path.stem.casefold() == "llama" else "server")
        writer_alias = os.environ.get("GAIDEN_QWEN_MODEL", "gaiden-writer").strip() or "gaiden-writer"
        embedding_alias = os.environ.get("GAIDEN_EMBEDDING_MODEL", "gaiden-embedding").strip() or "gaiden-embedding"
        if "," in writer_alias or "," in embedding_alias:
            raise ValueError("llama.cpp model aliases cannot contain commas")
        logs = runtime_root / "logs"
        thinking = os.environ.get("GAIDEN_QWEN_THINKING", "0").casefold() in {"1", "true", "yes", "on"}
        self.plan = plan
        self.writer = ManagedLlamaServer(executable=executable, model=plan.writer_model, alias=writer_alias, port=int(os.environ.get("GAIDEN_RUNTIME_WRITER_PORT", "8000")), context=plan.policy.writer_context, gpu_layers=plan.policy.writer_gpu_layers, fit=plan.policy.fit, parallel=plan.policy.parallel, log_path=logs / "llama-writer.log", cache_type_k=plan.policy.cache_type_k, cache_type_v=plan.policy.cache_type_v, reasoning="on" if thinking else "off")
        self.embedding = ManagedLlamaServer(executable=executable, model=plan.embedding_model, alias=embedding_alias, port=int(os.environ.get("GAIDEN_RUNTIME_EMBEDDING_PORT", "8001")), context=plan.policy.embedding_context, gpu_layers=plan.policy.embedding_gpu_layers, fit=plan.policy.fit, parallel=max(1, min(2, plan.policy.parallel)), log_path=logs / "llama-embedding.log", cache_type_k=plan.policy.cache_type_k, cache_type_v=plan.policy.cache_type_v, embedding=True, embedding_pooling=os.environ.get("GAIDEN_EMBEDDING_POOLING", "last").strip() or "last")
        atexit.register(self.stop)

    def start(self) -> dict[str, str]:
        self.embedding.start()
        try:
            self.writer.start()
        except Exception:
            self.embedding.stop()
            raise
        return self.environment()

    def environment(self) -> dict[str, str]:
        return {"GAIDEN_RUNTIME_ACTIVE": "llamacpp", "GAIDEN_QWEN_BASE_URL": self.writer.base_url, "GAIDEN_QWEN_API_KEY": "local-no-key", "GAIDEN_QWEN_MODEL": self.writer.alias, "GAIDEN_EMBEDDING_BASE_URL": self.embedding.base_url, "GAIDEN_EMBEDDING_API_KEY": "local-no-key", "GAIDEN_EMBEDDING_MODEL": self.embedding.alias, "GAIDEN_RUNTIME_WRITER_QUANT": self.plan.writer_quant, "GAIDEN_RUNTIME_EMBEDDING_QUANT": self.plan.embedding_quant}

    def stop(self) -> None:
        self.writer.stop()
        self.embedding.stop()
