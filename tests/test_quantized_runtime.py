from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from gaiden.runtime.hardware import HardwareProfile
from gaiden.runtime.manager import LlamaExecutable, ManagedLlamaServer
from gaiden.runtime.policy import plan_runtime, policy_for_hardware


def profile(vram: float, ram: float = 32.0) -> HardwareProfile:
    return HardwareProfile(
        platform="Test", machine="x86_64", cpu_count=8, ram_gb=ram,
        gpu_name="Test GPU" if vram else "", gpu_vram_gb=vram,
        gpu_count=1 if vram else 0, gpu_probe="test",
    )


class RuntimePolicyTests(TestCase):
    def test_six_gb_gpu_reserves_gpu_for_writer(self) -> None:
        policy = policy_for_hardware(profile(6.0))
        self.assertEqual(policy.writer_preference[0], "Q4_K_M")
        self.assertEqual(policy.embedding_gpu_layers, "0")
        self.assertGreaterEqual(policy.writer_context, 8192)

    def test_selects_models_by_role_and_quantization(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "writer").mkdir()
            (root / "embedding").mkdir()
            writer_q5 = root / "writer" / "Qwen3.5-9B-Q5_K_M.gguf"
            writer_q4 = root / "writer" / "Qwen3.5-9B-Q4_K_M.gguf"
            embedding = root / "embedding" / "Qwen3-Embedding-0.6B-Q8_0.gguf"
            for path in (writer_q5, writer_q4, embedding):
                path.write_bytes(b"x")
            with patch.dict(os.environ, {"GAIDEN_RUNTIME_BACKEND": "auto"}, clear=False):
                plan = plan_runtime(model_root=root, executable="/opt/llama-server", profile=profile(6.0))
            self.assertEqual(plan.backend, "llamacpp")
            self.assertEqual(plan.writer_model, writer_q4.resolve())
            self.assertEqual(plan.embedding_model, embedding.resolve())

    def test_explicit_missing_model_does_not_make_local_runtime_ready(self) -> None:
        with TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"GAIDEN_WRITER_GGUF": "missing.gguf"}, clear=False):
                plan = plan_runtime(model_root=Path(temporary), executable="/opt/llama-server", profile=profile(6.0))
            self.assertFalse(plan.local_ready)
            self.assertEqual(plan.backend, "openai")


class CommandTests(TestCase):
    def test_writer_server_uses_loopback_auto_fit_and_reasoning(self) -> None:
        server = ManagedLlamaServer(
            executable=LlamaExecutable("/tmp/llama-server", "server"),
            model=Path("/tmp/model-Q4_K_M.gguf"), alias="gaiden-writer", port=8000,
            context=2048, gpu_layers="auto", fit=True, parallel=1,
            log_path=Path("/tmp/gaiden-test.log"), reasoning="off",
        )
        command = server.command()
        self.assertIn("127.0.0.1", command)
        self.assertIn("-fit", command)
        self.assertIn("auto", command)
        self.assertIn("--reasoning", command)

    def test_embedding_server_enables_embedding_mode(self) -> None:
        server = ManagedLlamaServer(
            executable=LlamaExecutable("/tmp/llama-server", "server"),
            model=Path("/tmp/embed-Q8_0.gguf"), alias="gaiden-embedding", port=8001,
            context=2048, gpu_layers="0", fit=True, parallel=1,
            log_path=Path("/tmp/gaiden-test.log"), embedding=True,
        )
        self.assertIn("--embedding", server.command())
