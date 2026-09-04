"""Hardware-aware local/remote inference runtime for Gaiden."""

from .hardware import HardwareProfile, detect_hardware
from .policy import QuantizationPolicy, RuntimePlan, plan_runtime


def build_embedder(*args, **kwargs):
    from .factory import build_embedder as _build_embedder

    return _build_embedder(*args, **kwargs)


def build_generator(*args, **kwargs):
    from .factory import build_generator as _build_generator

    return _build_generator(*args, **kwargs)


def runtime_summary(*args, **kwargs):
    from .factory import runtime_summary as _runtime_summary

    return _runtime_summary(*args, **kwargs)


__all__ = [
    "HardwareProfile",
    "QuantizationPolicy",
    "RuntimePlan",
    "build_embedder",
    "build_generator",
    "detect_hardware",
    "plan_runtime",
    "runtime_summary",
]
