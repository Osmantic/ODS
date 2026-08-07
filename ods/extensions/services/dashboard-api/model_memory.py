"""Shared context-aware memory estimates for model selection and activation."""

from __future__ import annotations

import math
import re
from typing import Any


def _positive_number(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def estimated_param_billions(model: dict[str, Any]) -> float:
    """Best-effort model scale from explicit metadata, name, then file size."""
    for key in ("total_params_b", "params_b"):
        value = _positive_number(model.get(key))
        if value:
            return value

    numbers: list[float] = []
    # config/model-library.json spells the filename `gguf_file`; the oracle's
    # normalized shape carries `gguf`. Read both — the filename is the only
    # place some entries state their scale, and losing it drops the estimate
    # onto the size heuristic, which disagrees with scripts/select-model.py.
    for text in (
        model.get("id"),
        model.get("name"),
        model.get("llm_model_name"),
        model.get("gguf"),
        model.get("gguf_file"),
    ):
        for match in re.findall(r"(\d+(?:\.\d+)?)\s*b", str(text or ""), re.I):
            val = _positive_number(match)
            if val:
                numbers.append(val)
    if numbers:
        return max(numbers)

    size_mb = _positive_number(model.get("size_mb"))
    if size_mb:
        # Q4_K_M GGUFs are roughly 0.55-0.65 GiB per billion parameters.
        return max(size_mb / 600.0, 1.0)
    return 4.0


def estimated_context_kv_gb(
    model: dict[str, Any],
    context_length: int | None = None,
) -> float:
    """Estimate standard llama.cpp KV pressure at the selected context."""
    try:
        context = int(
            context_length
            if context_length is not None
            else model.get("context_length") or 0
        )
    except (TypeError, ValueError):
        context = 0
    context = max(context, 8192)
    params_b = estimated_param_billions(model)
    kv_per_32k_gb = min(max(params_b * 0.12, 0.35), 3.5)
    return round(kv_per_32k_gb * (context / 32768.0), 2)


def required_model_memory_gb(
    model: dict[str, Any],
    *,
    context_length: int | None = None,
    weight_size_mb: int | float | None = None,
    runtime_profile: dict[str, Any] | None = None,
) -> float:
    """Return the shared selector/activation memory requirement.

    A matching runtime profile is authoritative because profiles may describe
    CPU offload or a specialized cache implementation that intentionally uses
    less GPU memory than the generic estimate. Without one, the estimate never
    drops below the declared catalog contract or weight-plus-KV requirement.
    """
    if isinstance(runtime_profile, dict):
        profile_gb = _positive_number(runtime_profile.get("estimated_required_gb"))
        if profile_gb:
            return round(profile_gb, 2)

    declared_gb = _positive_number(model.get("vram_required_gb"))
    size_mb = _positive_number(
        weight_size_mb if weight_size_mb is not None else model.get("size_mb")
    )
    size_and_kv_gb = (
        (size_mb / 1024.0) + estimated_context_kv_gb(model, context_length)
        if size_mb
        else 0.0
    )
    return round(max(declared_gb, size_and_kv_gb), 2)
