from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass

_GIB = 1024**3


@dataclass(frozen=True)
class HardwareProfile:
    platform: str
    machine: str
    cpu_count: int
    ram_gb: float
    gpu_name: str = ""
    gpu_vram_gb: float = 0.0
    gpu_count: int = 0
    gpu_probe: str = "none"

    @property
    def has_gpu(self) -> bool:
        return self.gpu_count > 0 and self.gpu_vram_gb > 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def _system_ram_bytes() -> int:
    override = os.environ.get("GAIDEN_RAM_GB", "").strip()
    if override:
        try:
            return int(float(override) * _GIB)
        except ValueError:
            pass
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
    try:
        return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return 0


def _nvidia_probe() -> tuple[str, float, int, str]:
    manual = os.environ.get("GAIDEN_GPU_VRAM_GB", "").strip()
    if manual:
        try:
            return (
                os.environ.get("GAIDEN_GPU_NAME", "manual GPU").strip() or "manual GPU",
                float(manual), 1, "env",
            )
        except ValueError:
            pass
    binary = shutil.which("nvidia-smi")
    if not binary:
        return "", 0.0, 0, "none"
    try:
        result = subprocess.run(
            [binary, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2.0, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "", 0.0, 0, "nvidia-smi-error"
    rows: list[tuple[str, float]] = []
    for raw in result.stdout.splitlines():
        if "," not in raw:
            continue
        name, memory = raw.rsplit(",", 1)
        try:
            rows.append((name.strip(), float(memory.strip()) / 1024.0))
        except ValueError:
            continue
    if not rows:
        return "", 0.0, 0, "nvidia-smi-empty"
    name, vram = max(rows, key=lambda row: row[1])
    return name, vram, len(rows), "nvidia-smi"


def detect_hardware() -> HardwareProfile:
    gpu_name, gpu_vram_gb, gpu_count, gpu_probe = _nvidia_probe()
    ram_bytes = _system_ram_bytes()
    return HardwareProfile(
        platform=platform.system() or os.name,
        machine=platform.machine(),
        cpu_count=max(1, os.cpu_count() or 1),
        ram_gb=round(ram_bytes / _GIB, 2) if ram_bytes else 0.0,
        gpu_name=gpu_name,
        gpu_vram_gb=round(gpu_vram_gb, 2),
        gpu_count=gpu_count,
        gpu_probe=gpu_probe,
    )
