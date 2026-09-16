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
        numbers.extend(
            float(match)
            for match in re.findall(r"(\d+(?:\.\d+)?)\s*b", str(text or ""), re.I)
        )
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
    block_count = _positive_number(model.get("block_count"))
    kv_heads_raw = model.get("attention_head_count_kv") or model.get("head_count_kv")
    embedding_length = _positive_number(model.get("embedding_length"))
    head_count = _positive_number(
        model.get("attention_head_count") or model.get("head_count")
    )
    head_dimension = _positive_number(
        model.get("attention_head_dimension")
        or model.get("head_dimension")
    )
    derived_head_dimension = (
        embedding_length / head_count if embedding_length and head_count else 0.0
    )
    key_dimension = _positive_number(model.get("attention_key_length"))
    value_dimension = _positive_number(model.get("attention_value_length"))
    key_dimension = key_dimension or head_dimension or derived_head_dimension
    value_dimension = value_dimension or head_dimension or derived_head_dimension

    layer_kv_heads = 0.0
    if isinstance(kv_heads_raw, (list, tuple)):
        kv_heads_by_layer = [_positive_number(value) for value in kv_heads_raw]
        # Per-layer arrays are authoritative only when complete. The GGUF
        # inspector deliberately samples very large arrays, so an incomplete
        # list must fall back instead of under-counting omitted layers.
        if block_count and len(kv_heads_by_layer) == int(block_count):
            layer_kv_heads = sum(kv_heads_by_layer)
    else:
        kv_heads = _positive_number(kv_heads_raw)
        if block_count and kv_heads:
            layer_kv_heads = block_count * kv_heads

    if layer_kv_heads and key_dimension and value_dimension:
        # llama.cpp's default f16 KV cache stores one key and one value for
        # every KV head/token. Key and value dimensions can differ, and newer
        # hybrid architectures expose a per-layer KV-head array.
        element_bytes = _positive_number(model.get("kv_cache_element_bytes")) or 2.0
        kv_bytes = (
            layer_kv_heads
            * (key_dimension + value_dimension)
            * element_bytes
            * context
        )
        return round(kv_bytes / (1024.0 ** 3), 2)

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
