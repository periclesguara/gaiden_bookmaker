#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gaiden.runtime.hardware import detect_hardware
from gaiden.runtime.manager import QuantizedRuntimeAdapter, find_llama_executable
from gaiden.runtime.policy import plan_runtime


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def build_plan():
    executable = find_llama_executable()
    model_root = Path(os.environ.get("GAIDEN_MODEL_ROOT", ".runtime/models")).expanduser()
    if not model_root.is_absolute():
        model_root = ROOT / model_root
    return plan_runtime(model_root=model_root, executable=executable.path if executable else "", profile=detect_hardware())


def command_plan(_args: argparse.Namespace) -> int:
    print(json.dumps(build_plan().to_dict(), indent=2))
    return 0


def command_serve(_args: argparse.Namespace) -> int:
    plan = build_plan()
    if plan.backend != "llamacpp" or not plan.local_ready:
        print(json.dumps(plan.to_dict(), indent=2))
        print("Local runtime is not ready. Add GGUF models and install llama.cpp.", file=sys.stderr)
        return 2
    adapter = QuantizedRuntimeAdapter(plan, runtime_root=ROOT / ".runtime")
    print(json.dumps({"status": "ready", "runtime": plan.to_dict(), "environment": adapter.start()}, indent=2))
    try:
        import time
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        adapter.stop()
    return 0


def main() -> int:
    load_env(ROOT / ".env")
    load_env(ROOT / ".env.local")
    parser = argparse.ArgumentParser(description="Gaiden Quantized Runtime Adapter")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="show hardware, quantization and backend plan").set_defaults(func=command_plan)
    commands.add_parser("serve", help="start managed llama.cpp writer and embedding servers").set_defaults(func=command_serve)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
