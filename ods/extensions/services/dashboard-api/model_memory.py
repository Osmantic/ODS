"""Shared context-aware memory estimates for model selection and activation.

scripts/select-model.py (installer), performance_oracle.py (dashboard) and
bin/ods-host-agent.py (activation) all answer "how much memory does this model
need at this context?" through this module, so they cannot disagree.

There are two estimate paths:

* **Architecture.** The catalog entry declares its attention layout:
  ``block_count``, KV heads, key/value lengths, which layers hold a KV cache
  (``attention_layer_count``, a per-layer ``attention_head_count_kv`` array, or
  ``full_attention_interval``) and ``recurrent_state_bytes`` (0 for a dense
  model). ``recurrent_state_bytes`` is the marker that the layout was reviewed:
  without it a hybrid model cannot be told apart from a dense one. The estimate
  is weights + KV on attention layers only + per-sequence recurrent state +
  compute overhead, and it is authoritative (``vram_required_gb`` is not a
  floor). Sliding-window (SWA) layers, declared with ``sliding_window`` and
  the ``sliding_window_*`` keys, hold only the window, not the context: the
  ``attention_*`` keys then describe the full-attention layers alone.
* **Legacy.** Everything else (imports, unknown GGUFs, entries not reviewed
  yet) keeps the historical estimate exactly: file size + KV (from metadata or
  a parameter-count heuristic), floored by ``vram_required_gb``.

Discrete GPUs have a second, stricter question: does llama.cpp keep every
layer on the GPU? The "Full GPU residency" section below answers it with
llama.cpp's own device-memory projection (:func:`resident_configuration`),
built from the same KV formula (:func:`kv_bytes_per_token`, the
``sliding_window_*`` helpers) and the same recurrent-state layout as
:func:`estimate_model_memory`, plus the catalog's measured or GGUF-derived
``gpu_residency`` data (weights actually placed on the GPU, vocabulary for
the compute buffer). Do not re-derive KV anywhere else.
"""

from __future__ import annotations

import math
import os
import platform as _platform
import re
import sys
from typing import Any, Iterable, NamedTuple


MEMORY_METADATA_KEYS = (
    "total_params_b", "params_b", "block_count", "embedding_length",
    "attention_head_count", "head_count", "attention_head_count_kv",
    "head_count_kv", "attention_head_dimension", "head_dimension",
    "attention_key_length", "attention_value_length", "kv_cache_element_bytes",
    "attention_layer_count", "full_attention_interval", "recurrent_state_bytes",
    "size_bytes", "max_context_length",
    "sliding_window", "sliding_window_layer_count", "sliding_window_head_count_kv",
    "sliding_window_key_length", "sliding_window_value_length",
    "vocab_size", "gpu_residency",
)

MIB = 1024.0 ** 2
GIB = 1024.0 ** 3

# Bytes per cached element for llama.cpp --cache-type-k/--cache-type-v. Block
# quantized types store a 2-byte scale per 32 elements (q8_0: 32 + 2 bytes).
KV_CACHE_BYTES_PER_ELEMENT = {
    "f32": 4.0,
    "f16": 2.0,
    "bf16": 2.0,
    "q8_0": 34 / 32,
    "q5_1": 24 / 32,
    "q5_0": 22 / 32,
    "q4_1": 20 / 32,
    "q4_0": 18 / 32,
    "iq4_nl": 18 / 32,
}
# Compute buffers, output logits and CUDA/Metal context, calibrated against
# Qwen3.5-27B Q4_K_M on an RTX 5090 at llama.cpp b9014 (20,620 MiB at 64K f16,
# 24,716 MiB at 128K f16, 18,742 MiB at 64K q8_0).
OVERHEAD_BASE_GIB = 0.35
OVERHEAD_PER_WEIGHT_GIB = 0.015
# llama.cpp b9014 default --ctx-checkpoints (common.h n_ctx_checkpoints).
LLAMA_DEFAULT_CTX_CHECKPOINTS = 32
# llama.cpp b9014 sizes a sliding-window cache at n_swa * n_seq + n_ubatch
# cells, padded to 256 and capped at the context (llama-kv-cache-iswa.cpp);
# the default --ubatch-size is 512 and ODS does not change it.
SWA_UBATCH_CELLS = 512
SWA_CELL_PADDING = 256
# Legacy and authored (runtime-profile) estimates keep the historical
# tolerance for GPUs that report slightly under their marketed size.
LEGACY_FIT_TOLERANCE_GIB = 0.25
MINIMUM_CONTEXT = 8192
CONTEXT_STEPS = (8192, 16384, 32768, 65536, 131072, 262144)

MEMORY_CLASSES = ("discrete", "unified", "cpu")


def memory_metadata(model: dict[str, Any]) -> dict[str, Any]:
    """Preserve architecture inputs when normalizing catalog records."""
    return {key: model[key] for key in MEMORY_METADATA_KEYS if key in model}


def _positive_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _non_negative_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


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


def _context(model: dict[str, Any], context_length: int | None) -> int:
    try:
        context = int(
            context_length
            if context_length is not None
            else model.get("context_length") or 0
        )
    except (TypeError, ValueError):
        context = 0
    return max(context, MINIMUM_CONTEXT)


def _kv_dimensions(model: dict[str, Any]) -> tuple[float, float]:
    embedding_length = _positive_number(model.get("embedding_length"))
    head_count = _positive_number(
        model.get("attention_head_count") or model.get("head_count")
    )
    head_dimension = _positive_number(
        model.get("attention_head_dimension") or model.get("head_dimension")
    )
    derived = embedding_length / head_count if embedding_length and head_count else 0.0
    key_dimension = _positive_number(model.get("attention_key_length"))
    value_dimension = _positive_number(model.get("attention_value_length"))
    return (
        key_dimension or head_dimension or derived,
        value_dimension or head_dimension or derived,
    )


def _complete_per_layer_kv_heads(model: dict[str, Any]) -> list[float] | None:
    raw = model.get("attention_head_count_kv") or model.get("head_count_kv")
    if not isinstance(raw, (list, tuple)):
        return None
    block_count = _positive_number(model.get("block_count"))
    heads = [_positive_number(value) for value in raw]
    # Per-layer arrays are authoritative only when complete. The GGUF
    # inspector deliberately samples very large arrays, so an incomplete list
    # must fall back instead of under-counting omitted layers.
    if block_count and len(heads) == int(block_count):
        return heads
    return None


def kv_layer_count(model: dict[str, Any]) -> int | None:
    """Number of layers that hold a KV cache (hybrid SSM layers hold none).

    Precedence: ``attention_layer_count``; a complete per-layer
    ``attention_head_count_kv`` array (zero entries are SSM layers);
    ``block_count // full_attention_interval``; ``block_count`` (dense).
    """
    block_count = int(_positive_number(model.get("block_count")))
    if not block_count:
        return None
    explicit = int(_positive_number(model.get("attention_layer_count")))
    if explicit:
        return min(explicit, block_count)
    per_layer = _complete_per_layer_kv_heads(model)
    if per_layer is not None:
        return sum(1 for heads in per_layer if heads > 0)
    raw = model.get("attention_head_count_kv") or model.get("head_count_kv")
    if isinstance(raw, (list, tuple)):
        return None
    interval = int(_positive_number(model.get("full_attention_interval")))
    if interval > 1:
        return block_count // interval
    return block_count


def _kv_head_layers(model: dict[str, Any]) -> float:
    """Sum of KV heads over the layers that hold a KV cache."""
    per_layer = _complete_per_layer_kv_heads(model)
    if per_layer is not None:
        return float(sum(per_layer))
    raw = model.get("attention_head_count_kv") or model.get("head_count_kv")
    if isinstance(raw, (list, tuple)):
        return 0.0
    heads = _positive_number(raw)
    layers = kv_layer_count(model)
    return heads * layers if heads and layers else 0.0


def _cache_element_bytes(cache_type: object) -> float:
    key = str(cache_type or "f16").strip().lower()
    # Unknown cache types are charged as f16, the llama.cpp default.
    return KV_CACHE_BYTES_PER_ELEMENT.get(key, 2.0)


def kv_bytes_per_token(
    model: dict[str, Any],
    cache_type_k: str = "f16",
    cache_type_v: str = "f16",
) -> float | None:
    """KV-cache bytes per context token, or None when metadata is incomplete."""
    head_layers = _kv_head_layers(model)
    key_dimension, value_dimension = _kv_dimensions(model)
    if not (head_layers and key_dimension and value_dimension):
        return None
    return head_layers * (
        key_dimension * _cache_element_bytes(cache_type_k)
        + value_dimension * _cache_element_bytes(cache_type_v)
    )


def sliding_window_kv_bytes_per_cell(
    model: dict[str, Any],
    cache_type_k: str = "f16",
    cache_type_v: str = "f16",
) -> float:
    """KV bytes per cached cell summed over the sliding-window layers (0 if none)."""
    window = _positive_number(model.get("sliding_window"))
    layers = _positive_number(model.get("sliding_window_layer_count"))
    heads = _positive_number(model.get("sliding_window_head_count_kv"))
    key_dimension = _positive_number(model.get("sliding_window_key_length"))
    value_dimension = _positive_number(model.get("sliding_window_value_length")) or key_dimension
    if not (window and layers and heads and key_dimension):
        return 0.0
    return layers * heads * (
        key_dimension * _cache_element_bytes(cache_type_k)
        + value_dimension * _cache_element_bytes(cache_type_v)
    )


def sliding_window_cells(model: dict[str, Any], context_length: int, parallel: int = 1) -> int:
    """Cells llama.cpp allocates for the sliding-window cache at this context."""
    window = int(_positive_number(model.get("sliding_window")))
    if not window:
        return 0
    cells = window * max(int(parallel or 1), 1) + SWA_UBATCH_CELLS
    padded = -(-cells // SWA_CELL_PADDING) * SWA_CELL_PADDING
    return min(int(context_length), padded)


def architecture_metadata_complete(model: dict[str, Any]) -> bool:
    """True when the entry carries a reviewed attention layout (see module doc)."""
    return (
        kv_bytes_per_token(model) is not None
        and _non_negative_number(model.get("recurrent_state_bytes")) is not None
        and _weights_bytes(model) > 0
    )


def _weights_bytes(model: dict[str, Any], weight_size_mb: int | float | None = None) -> float:
    explicit = _positive_number(weight_size_mb)
    if explicit:
        return explicit * MIB
    size_bytes = _positive_number(model.get("size_bytes"))
    if size_bytes:
        return size_bytes
    return _positive_number(model.get("size_mb")) * MIB


class MemoryEstimate(NamedTuple):
    """One model at one context and cache configuration.

    ``device_gib`` is what must fit in VRAM (or in RAM on the CPU backend);
    ``total_gib`` adds the host-side context checkpoints llama.cpp keeps for
    recurrent (hybrid/SSM) layers. All sizes are GiB. A NamedTuple rather
    than a dataclass: bin/ods-host-agent.py loads this file without
    registering it in sys.modules, which dataclasses require.
    """

    context_length: int
    cache_type_k: str
    cache_type_v: str
    layers: int | None
    kv_layers: int | None
    kv_bytes_per_token: float | None
    weights_gib: float
    kv_gib: float
    recurrent_state_gib: float
    compute_overhead_gib: float
    host_checkpoint_gib: float
    device_gib: float
    total_gib: float
    method: str
    # Part of ``kv_gib`` held by sliding-window layers (fixed by the window,
    # not the context). Last, so positional users of the tuple keep working.
    swa_kv_gib: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return dict(self._asdict())


def estimate_model_memory(
    model: dict[str, Any],
    *,
    context_length: int | None = None,
    cache_type_k: str = "f16",
    cache_type_v: str = "f16",
    parallel: int = 1,
    ctx_checkpoints: int | None = None,
    weight_size_mb: int | float | None = None,
) -> MemoryEstimate:
    """Estimate memory for ``model`` at ``context_length``.

    llama.cpp's ``--ctx-size`` is the total across slots, so the KV cache is
    not multiplied by ``parallel``; recurrent state is per sequence and is.
    A sliding-window cache holds ``n_swa * parallel + n_ubatch`` cells (see
    :func:`sliding_window_cells`). Context checkpoints, kept in host RAM,
    copy each sequence's recurrent and sliding-window state.
    ``weight_size_mb`` (the file on disk, MiB) overrides catalog sizes.
    """
    context = _context(model, context_length)
    cache_k = str(cache_type_k or "f16").strip().lower()
    cache_v = str(cache_type_v or "f16").strip().lower()
    layers = int(_positive_number(model.get("block_count"))) or None
    kv_layers = kv_layer_count(model)

    if architecture_metadata_complete(model):
        per_token = kv_bytes_per_token(model, cache_k, cache_v) or 0.0
        weights = _weights_bytes(model, weight_size_mb) / GIB
        sequences = max(int(parallel or 1), 1)
        swa_per_cell = sliding_window_kv_bytes_per_cell(model, cache_k, cache_v)
        swa_kv = swa_per_cell * sliding_window_cells(model, context, sequences) / GIB
        kv = per_token * context / GIB + swa_kv
        state_bytes = _non_negative_number(model.get("recurrent_state_bytes")) or 0.0
        recurrent = state_bytes * sequences / GIB
        overhead = OVERHEAD_BASE_GIB + OVERHEAD_PER_WEIGHT_GIB * weights
        checkpoints = (
            LLAMA_DEFAULT_CTX_CHECKPOINTS
            if ctx_checkpoints is None
            else max(int(ctx_checkpoints), 0)
        )
        swa_state_bytes = swa_per_cell * sliding_window_cells(model, context, 1)
        host = checkpoints * (state_bytes + swa_state_bytes) * sequences / GIB
        device = weights + kv + recurrent + overhead
        return MemoryEstimate(
            context_length=context,
            cache_type_k=cache_k,
            cache_type_v=cache_v,
            layers=layers,
            kv_layers=kv_layers,
            kv_bytes_per_token=per_token,
            weights_gib=round(weights, 3),
            kv_gib=round(kv, 3),
            recurrent_state_gib=round(recurrent, 3),
            compute_overhead_gib=round(overhead, 3),
            host_checkpoint_gib=round(host, 3),
            device_gib=round(device, 2),
            total_gib=round(device + host, 2),
            method="architecture",
            swa_kv_gib=round(swa_kv, 3),
        )

    # Legacy path: bit-for-bit the historical estimate (size_mb, not
    # size_bytes; KV rounded to 0.01; the declared catalog value as a floor).
    kv = estimated_context_kv_gb(model, context)
    size_mb = _positive_number(
        weight_size_mb if weight_size_mb is not None else model.get("size_mb")
    )
    weights = size_mb / 1024.0
    size_and_kv = (weights + kv) if size_mb else 0.0
    device = round(max(_positive_number(model.get("vram_required_gb")), size_and_kv), 2)
    return MemoryEstimate(
        context_length=context,
        cache_type_k=cache_k,
        cache_type_v=cache_v,
        layers=layers,
        kv_layers=kv_layers,
        kv_bytes_per_token=kv_bytes_per_token(model),
        weights_gib=round(weights, 3),
        kv_gib=kv,
        recurrent_state_gib=0.0,
        compute_overhead_gib=0.0,
        host_checkpoint_gib=0.0,
        device_gib=device,
        total_gib=device,
        method="legacy-heuristic",
    )


def estimated_context_kv_gb(
    model: dict[str, Any],
    context_length: int | None = None,
) -> float:
    """Estimate standard llama.cpp f16 KV pressure at the selected context."""
    context = _context(model, context_length)
    per_token = kv_bytes_per_token(model)
    if per_token is not None:
        # llama.cpp's default f16 KV cache stores one key and one value for
        # every KV head/token on each attention layer. An explicit element
        # size (legacy catalog field) replaces the f16 default.
        element_bytes = _positive_number(model.get("kv_cache_element_bytes")) or 2.0
        return round(per_token * (element_bytes / 2.0) * context / GIB, 2)

    params_b = estimated_param_billions(model)
    kv_per_32k_gb = min(max(params_b * 0.12, 0.35), 3.5)
    return round(kv_per_32k_gb * (context / 32768.0), 2)


def runtime_profile_cache_settings(
    runtime_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """Cache types, checkpoint count and slot count a runtime profile sets."""
    env = (runtime_profile or {}).get("env") if isinstance(runtime_profile, dict) else None
    env = env if isinstance(env, dict) else {}

    def _int(key: str) -> int | None:
        try:
            return int(str(env.get(key)).strip())
        except (TypeError, ValueError):
            return None

    return {
        "cache_type_k": str(env.get("LLAMA_ARG_CACHE_TYPE_K") or "f16"),
        "cache_type_v": str(env.get("LLAMA_ARG_CACHE_TYPE_V") or "f16"),
        "ctx_checkpoints": _int("LLAMA_ARG_CTX_CHECKPOINTS"),
        "parallel": _int("LLAMA_PARALLEL") or 1,
    }


def authored_profile_estimate(runtime_profile: dict[str, Any] | None) -> float:
    """A runtime profile's hand-measured ``estimated_required_gb`` (0 if none)."""
    if not isinstance(runtime_profile, dict):
        return 0.0
    return _positive_number(runtime_profile.get("estimated_required_gb"))


def estimate_for_runtime(
    model: dict[str, Any],
    *,
    context_length: int | None = None,
    weight_size_mb: int | float | None = None,
    runtime_profile: dict[str, Any] | None = None,
) -> MemoryEstimate:
    """:func:`estimate_model_memory` with a runtime profile's cache settings."""
    if context_length is None and isinstance(runtime_profile, dict) and runtime_profile.get("context_length"):
        try:
            context_length = int(runtime_profile["context_length"])
        except (TypeError, ValueError):
            context_length = None
    settings = runtime_profile_cache_settings(runtime_profile)
    return estimate_model_memory(
        model,
        context_length=context_length,
        cache_type_k=settings["cache_type_k"],
        cache_type_v=settings["cache_type_v"],
        parallel=settings["parallel"],
        ctx_checkpoints=settings["ctx_checkpoints"],
        weight_size_mb=weight_size_mb,
    )


def required_model_memory_gb(
    model: dict[str, Any],
    *,
    context_length: int | None = None,
    weight_size_mb: int | float | None = None,
    runtime_profile: dict[str, Any] | None = None,
    include_host_state: bool = False,
) -> float:
    """Return the shared selector/activation memory requirement in GiB.

    A matching runtime profile's ``estimated_required_gb`` is authoritative
    because profiles may describe CPU offload or a specialized cache that uses
    less GPU memory than the generic estimate. Otherwise the profile's cache
    types and checkpoint count feed :func:`estimate_model_memory`. The result
    is ``device_gib``, or ``total_gib`` with ``include_host_state`` (the CPU
    backend, where host checkpoints share the same RAM).
    """
    authored = authored_profile_estimate(runtime_profile)
    if authored:
        return round(authored, 2)
    estimate = estimate_for_runtime(
        model,
        context_length=context_length,
        weight_size_mb=weight_size_mb,
        runtime_profile=runtime_profile,
    )
    return estimate.total_gib if include_host_state else estimate.device_gib


def fit_margin_gib(
    capacity_gib: float,
    memory_class: str,
    *,
    gpu_platform: str | None = None,
    gpu_count: int = 1,
) -> float:
    """Free memory an architecture estimate must leave on this memory class.

    Discrete GPUs keep the memory a fresh llama.cpp process cannot use: the
    driver reserve, its CUDA context and, under WDDM, the memory Windows
    withholds from CUDA (:func:`platform_reserve_mib`, calibrated on the
    fleet). This is the capacity rule for discrete candidates the residency
    estimate may not decide (see :func:`residency_is_decisive`); a decisive
    candidate must pass :func:`resident_configuration` instead. Unified
    memory already uses a 55% share of RAM, and the CPU capacity is already
    a bounded share of RAM, so neither adds a margin.
    """
    if memory_class != "discrete":
        return 0.0
    capacity_mib = _positive_number(capacity_gib) * 1024.0
    if not capacity_mib:
        return 0.0
    count = max(int(gpu_count or 1), 1)
    reserve = platform_reserve_mib(capacity_mib / count, gpu_platform) * count
    return round(reserve / 1024.0, 2)


def memory_fits(
    required_gib: float,
    capacity_gib: float,
    memory_class: str,
    *,
    architecture_estimate: bool,
    gpu_platform: str | None = None,
    gpu_count: int = 1,
) -> bool:
    """Apply the capacity rule for one requirement.

    Architecture estimates must leave :func:`fit_margin_gib` free. Legacy and
    authored (runtime-profile) estimates keep the historical +0.25 GiB
    tolerance so validated profiles behave exactly as before.
    """
    if architecture_estimate:
        margin = fit_margin_gib(
            capacity_gib, memory_class, gpu_platform=gpu_platform, gpu_count=gpu_count,
        )
        return required_gib <= capacity_gib - margin + 1e-9
    return required_gib <= capacity_gib + LEGACY_FIT_TOLERANCE_GIB


def context_candidates(
    model: dict[str, Any],
    *,
    min_context: int = 0,
) -> list[int]:
    """Contexts to try, largest first, for a model without a runtime profile.

    Starts at the catalog ``context_length`` (the operating default) and never
    goes above it, unless ``min_context`` needs more and ``max_context_length``
    allows it. Only entries with layer metadata (``block_count``) step down;
    other entries are offered at their catalog context only.
    """
    try:
        default = int(model.get("context_length") or 0)
    except (TypeError, ValueError):
        default = 0
    if not _positive_number(model.get("block_count")):
        return [max(default, 0)]
    try:
        native = int(model.get("max_context_length") or default)
    except (TypeError, ValueError):
        native = default
    target = default
    if min_context and default < min_context <= max(native, default):
        target = int(min_context)
    if target <= MINIMUM_CONTEXT:
        return [target]
    choices = {target, *(step for step in CONTEXT_STEPS if step <= target)}
    return sorted(choices, reverse=True)


def context_fitting_model(
    model: dict[str, Any],
    capacity_gb: float,
    *,
    tolerance_gb: float = LEGACY_FIT_TOLERANCE_GIB,
    min_context: int = 0,
    memory_class: str | None = None,
) -> dict[str, Any]:
    """Pick the largest context that fits, using architecture metadata only.

    Never touches a measured runtime profile. Leaves entries without layer
    metadata unchanged; the ranker still checks fit afterward, including when
    even the minimum context cannot fit. Contexts below ``min_context`` are
    tried only after every context at or above it failed. Without
    ``memory_class`` the historical ``tolerance_gb`` rule applies to every
    estimate.
    """
    if model.get("_runtime_profile") or not _positive_number(model.get("block_count")):
        return model
    if not _positive_number(capacity_gb):
        return model
    choices = context_candidates(model, min_context=min_context)
    default = int(model.get("context_length") or 0)
    if len(choices) <= 1 and (not choices or choices[0] == default):
        return model
    include_host = memory_class == "cpu"
    ordered = [c for c in choices if c >= min_context] + [c for c in choices if c < min_context]
    for context in ordered:
        estimate = estimate_model_memory(model, context_length=context)
        required = estimate.total_gib if include_host else estimate.device_gib
        if memory_class is None:
            fits = required <= capacity_gb + tolerance_gb
        else:
            fits = memory_fits(
                required, capacity_gb, memory_class,
                architecture_estimate=estimate.method == "architecture",
            )
        if fits:
            if context == default:
                return model
            return {
                **model,
                "max_context_length": model.get("max_context_length") or default,
                "context_length": context,
            }
    return model


# ---------------------------------------------------------------------------
# Full GPU residency on discrete GPUs
# ---------------------------------------------------------------------------
#
# ODS requires the selected model to be fully GPU-resident at the configured
# context. llama.cpp's automatic placement (``--n-gpu-layers auto``) keeps
# ``--fit-target`` MiB (default 1024) free on each device and otherwise moves
# layers to the CPU without failing, which is how an 8GB laptop silently ran
# 29/33 layers at a third of its decode speed. These helpers model the same
# arithmetic llama.cpp uses so selection, the dashboard, and activation agree
# on what "fits" means, and so a partial load can be corrected.
#
# Entry points (keep callers on these; do not re-derive the arithmetic):
#
# - ``resident_configuration()`` is the one fit estimator for discrete GPUs:
#   "which configuration of this model stays fully on this GPU?" for
#   scripts/select-model.py (installer), performance_oracle.py (dashboard)
#   and bin/ods-host-agent.py (activation preflight). Inputs: the catalog
#   record, total VRAM (summed over ``gpu_count``), runtime profile, persisted
#   env, context, platform, memory other processes hold, whether context may
#   be traded, and the operator's pinned controls. Output: fits / overrides /
#   steps / contextLength / residency / idleFits / bestEffort (documented on
#   the function). A model selector gates on ``idleFits`` (the model, not
#   today's desktop, decides the pick) and launches with ``overrides``, or
#   with ``bestEffort`` when only memory held by other processes is short.
# - ``residency_is_decisive()`` says whether that answer may change which
#   model or context is picked (exact metadata on a calibrated GPU), or may
#   only tune ubatch, fit target and KV cache type.
# - ``gpu_residency_fit()`` evaluates one configuration without changing it
#   (context options, re-checks).
# - ``plan_residency_fallback()`` is the ladder both of them and the
#   host agent's post-load re-fit use; the host agent feeds it llama.cpp's own
#   logged projection instead of this module's estimate.
# - ``parse_llama_placement()`` reads where llama.cpp actually put the model.

# Bytes per KV element: the one table, shared with estimate_model_memory.
KV_CACHE_TYPE_BYTES = KV_CACHE_BYTES_PER_ELEMENT
# Quantized KV fallbacks in preference order (quality first).
KV_CACHE_FALLBACK_LADDER = ("q8_0", "q4_0")

# llama.cpp b9014 common/common.h: fit_params_target defaults to 1024 MiB.
LLAMA_DEFAULT_FIT_TARGET_MIB = 1024
LLAMA_DEFAULT_UBATCH = 512
# The runtime grows ~256 MiB past llama.cpp's own projection once a 64K
# context fills (flash attention converts quantized KV to f16 in a pool), so
# never plan with less free memory than this. Measured on the RTX 5070 Laptop
# (perf/laptop-gpu-residency-bench.md, V1f): 512 kept 1111 MiB free at 60K.
MIN_SAFE_FIT_TARGET_MIB = 512
RESIDENCY_FALLBACK_UBATCH = 256
# Late planner step, before a q4_0 KV cache: the compute buffer halves again
# (123 MiB for a 248K vocabulary). It buys room for a desktop or another app
# holding a few hundred MiB on an 8 GB card at the 64K agent floor.
RESIDENCY_MIN_UBATCH = 128
# The residency controls ODS writes itself, and the only values it writes.
# Any other value in .env is the operator's own, and activation keeps it.
RESIDENCY_CONTROL_KEYS = ("LLAMA_ARG_UBATCH", "LLAMA_ARG_FIT_TARGET")
RESIDENCY_MANAGED_VALUES = {
    "LLAMA_ARG_UBATCH": frozenset({str(RESIDENCY_FALLBACK_UBATCH), str(RESIDENCY_MIN_UBATCH)}),
    "LLAMA_ARG_FIT_TARGET": frozenset({str(MIN_SAFE_FIT_TARGET_MIB)}),
}
# Allowance between this estimate and llama.cpp's own projection.
RESIDENCY_PLAN_GUARD_MIB = 64
STANDARD_CONTEXT_LENGTHS = CONTEXT_STEPS
# Agent/Hermes floor. A fallback never trades context below it, and never
# shrinks a context that was already below it.
RESIDENCY_CONTEXT_FLOOR = 65536

# The platform reserve below was calibrated on 8 GB and larger cards (RTX 5070
# Laptop 8151 MiB, RTX 5090 32607 MiB, RTX PRO 6000 97887 MiB per GPU). On
# smaller GPUs the residency estimate still configures, verifies and reports,
# but it never changes which model or context ODS picks, and activation does
# not refuse on it: nothing below 8 GB has been measured (no 4 GB card in the
# fleet). 7680 MiB admits every 8 GB class card (8151-8192 MiB reported).
RESIDENCY_CALIBRATED_MIN_VRAM_MIB = 7680


def residency_calibrated(total_vram_mb: object, gpu_count: int = 1) -> bool:
    """True when the per-GPU memory is inside the calibrated range."""
    per_gpu = _positive_number(total_vram_mb) / max(int(gpu_count or 1), 1)
    return per_gpu >= RESIDENCY_CALIBRATED_MIN_VRAM_MIB


def has_exact_residency_metadata(model: dict[str, Any]) -> bool:
    """True when the catalog carries measured or GGUF-derived residency data.

    Only then does the estimate reproduce llama.cpp's projection to the MiB.
    Selection lets residency change which model or context it picks only for
    such models on a calibrated GPU; everything else keeps the capacity fit
    and relies on the post-load placement check.
    """
    residency = model.get("gpu_residency") if isinstance(model.get("gpu_residency"), dict) else {}
    return bool(
        _positive_number(residency.get("gpu_weights_mib"))
        and _positive_number(residency.get("kv_bytes_per_token_f16"))
    )


def residency_is_decisive(model: dict[str, Any], total_vram_mb: object, gpu_count: int = 1) -> bool:
    """True when full residency may change which model or context is picked.

    Requires exact memory metadata for the model and a GPU inside the
    calibrated range. Otherwise selection keeps its capacity fit, and
    ``resident_configuration`` may only tune ubatch, fit target and KV cache
    type; the placement is verified after load and reported.
    """
    return residency_calibrated(total_vram_mb, gpu_count) and has_exact_residency_metadata(model)


# Marketing "8GB" cards report slightly under 8 GiB; the declared VRAM class
# of catalog entries without exact metadata keeps its historical tolerance.
DECLARED_VRAM_CLASS_TOLERANCE_GB = 0.25

# Conservative vocabulary/embedding sizes when a model carries no metadata.
_UNKNOWN_VOCAB_SIZE = 262144
_UNKNOWN_EMBEDDING_LENGTH = 8192

_NON_DISCRETE_BACKENDS = {"", "apple", "cpu", "none", "unknown", "cloud"}


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def is_discrete_gpu(backend: object, memory_type: object, total_vram_mb: object) -> bool:
    """True when the model must fit in dedicated GPU memory."""
    return (
        _key(backend) not in _NON_DISCRETE_BACKENDS
        and _key(memory_type) != "unified"
        and _positive_number(total_vram_mb) > 0
    )


def detect_gpu_platform() -> str:
    """Return linux, wsl, windows, or macos for GPU memory accounting.

    Containers report the host kernel, so a dashboard or host-agent container
    under WSL2 or Docker Desktop's WSL2 backend is correctly treated as WDDM.
    """
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return "wsl"
    try:
        release = _platform.release().lower()
    except OSError:
        release = ""
    return "wsl" if "microsoft" in release else "linux"


def platform_reserve_mib(total_mib_per_gpu: float, gpu_platform: str | None = None) -> int:
    """Device memory a fresh llama.cpp process cannot use on one GPU.

    This is ``nvidia-smi memory.total`` minus the free memory CUDA reports to
    llama.cpp at load: the driver reserve, the new process's CUDA context,
    and on WDDM (Windows and WSL) memory Windows withholds from CUDA.
    Calibrated from llama.cpp b9014 load logs on 2026-09-25 (total, then
    CUDA free at load): RTX 5090 native Linux 32607 -> 31431 (1176 per GPU),
    2x RTX PRO 6000 native Linux 195774 -> 192897 (1438 per GPU), RTX 5070
    Laptop under WSL 8151 -> 6860 (1291). The formula stays at or above every
    calibration point.
    """
    total = max(_positive_number(total_mib_per_gpu), 0.0)
    kind = (gpu_platform or detect_gpu_platform()).lower()
    driver_reserve = 322.0 + 0.00325 * total
    process_and_platform = 850.0 if kind == "linux" else 1000.0
    return int(math.ceil(driver_reserve + process_and_platform))


def kv_cache_type_bytes(cache_type: object) -> float:
    return _cache_element_bytes(cache_type)


def _positive_whole(value: object) -> int:
    number = _positive_number(value)
    return int(number) if number else 0


_TRUTHY_OFFLOAD_EXCLUDED = {"", "0", "false", "off", "no"}


def runtime_memory_settings(
    runtime_profile: dict[str, Any] | None = None,
    env: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the llama.cpp memory settings a launch will use.

    Residency overrides win over a runtime profile's env, which wins over the
    persisted .env, which wins over the compose/llama.cpp defaults (f16 KV,
    ubatch 512, fit target 1024 MiB).
    """
    profile_env = (
        runtime_profile.get("env")
        if isinstance(runtime_profile, dict) and isinstance(runtime_profile.get("env"), dict)
        else {}
    )
    values: dict[str, Any] = {}
    for source in (env or {}), profile_env, (overrides or {}):
        for key, value in source.items():
            if value is not None and str(value).strip() != "":
                values[str(key)] = value

    def text(key: str, default: str) -> str:
        return str(values.get(key) or default).strip().lower()

    intentional = any(
        str(values.get(key) or "").strip().lower() not in _TRUTHY_OFFLOAD_EXCLUDED
        for key in ("LLAMA_ARG_N_CPU_MOE", "LLAMA_ARG_CPU_MOE", "LLAMA_ARG_OVERRIDE_TENSOR")
    )
    return {
        "cacheTypeK": text("LLAMA_ARG_CACHE_TYPE_K", "f16"),
        "cacheTypeV": text("LLAMA_ARG_CACHE_TYPE_V", "f16"),
        "flashAttn": text("LLAMA_ARG_FLASH_ATTN", "auto"),
        "ubatch": _positive_whole(values.get("LLAMA_ARG_UBATCH")) or LLAMA_DEFAULT_UBATCH,
        "fitTargetMiB": (
            _positive_whole(values.get("LLAMA_ARG_FIT_TARGET")) or LLAMA_DEFAULT_FIT_TARGET_MIB
        ),
        "parallel": _positive_whole(values.get("LLAMA_PARALLEL")) or 1,
        "intentionalOffload": intentional,
    }


def estimated_device_memory_mib(
    model: dict[str, Any],
    *,
    context_length: int | None = None,
    cache_type_k: object = "f16",
    cache_type_v: object = "f16",
    ubatch: int = LLAMA_DEFAULT_UBATCH,
    parallel: int = 1,
    gpu_count: int = 1,
    weight_size_mb: int | float | None = None,
) -> dict[str, Any]:
    """Estimate llama.cpp's full-offload device memory projection in MiB.

    The projection is weights on the GPU + KV cache + recurrent state +
    compute buffer, the four parts of the ``projected to use N MiB of device
    memory`` line of llama.cpp b9014.

    * KV cache and recurrent state come from the catalog's attention layout,
      through the same :func:`kv_bytes_per_token` / sliding-window formula
      and ``recurrent_state_bytes`` as :func:`estimate_model_memory` (only
      attention layers hold a cache; a sliding-window layer holds its
      window). Entries without a layout fall back to ``gpu_residency``'s
      ``kv_bytes_per_token_f16`` / ``recurrent_state_mib``, then to the
      parameter-scale heuristic.
    * Weights: ``gpu_residency.gpu_weights_mib`` (measured, or read from the
      pinned GGUF: the token embedding stays in system memory) when present,
      otherwise the file size.
    * Compute buffer: one ubatch of f32 logits plus hidden state, from the
      vocabulary and embedding length.

    With ``gpu_residency`` data the result reproduces the fleet's load logs
    to the MiB (``basis`` ``measured`` or ``gguf``: an exact estimate);
    otherwise ``basis`` is ``architecture`` or ``declared`` and
    ``gpu_residency_fit`` also honours the catalog's declared VRAM class and
    runtime-profile estimate.
    """
    try:
        context = int(context_length or model.get("context_length") or 0)
    except (TypeError, ValueError):
        context = 0
    context = max(context, 1024)
    cache_k = str(cache_type_k or "f16").strip().lower()
    cache_v = str(cache_type_v or "f16").strip().lower()
    kv_factor = (kv_cache_type_bytes(cache_k) + kv_cache_type_bytes(cache_v)) / 4.0
    residency = model.get("gpu_residency") if isinstance(model.get("gpu_residency"), dict) else {}
    parallel = max(int(parallel or 1), 1)
    gpu_count = max(int(gpu_count or 1), 1)

    per_token = kv_bytes_per_token(model, cache_k, cache_v)
    residency_weights = _positive_number(residency.get("gpu_weights_mib"))
    residency_kv_f16 = _positive_number(residency.get("kv_bytes_per_token_f16"))
    if per_token is not None:
        swa_bytes = (
            sliding_window_kv_bytes_per_cell(model, cache_k, cache_v)
            * sliding_window_cells(model, context, parallel)
        )
        kv_mib = (per_token * context + swa_bytes) / MIB
    elif residency_kv_f16:
        kv_mib = residency_kv_f16 * context * kv_factor / MIB
    else:
        params_b = estimated_param_billions(model)
        kv_per_32k_gb = min(max(params_b * 0.12, 0.35), 3.5)
        kv_mib = kv_per_32k_gb * 1024.0 * (context / 32768.0) * kv_factor
    state_bytes = _non_negative_number(model.get("recurrent_state_bytes"))
    if state_bytes is not None:
        recurrent_mib = state_bytes / MIB * parallel
    else:
        recurrent_mib = _positive_number(residency.get("recurrent_state_mib")) * parallel

    if residency_weights and (per_token is not None or residency_kv_f16):
        basis = str(residency.get("basis") or "measured")
        weights_mib = residency_weights
    else:
        basis = "architecture" if per_token is not None else "declared"
        weights_mib = _weights_bytes(model, weight_size_mb) / MIB
    vocab = _positive_number(residency.get("vocab_size") or model.get("vocab_size"))
    embedding = _positive_number(
        residency.get("embedding_length") or model.get("embedding_length")
    )

    vocab = vocab or _UNKNOWN_VOCAB_SIZE
    embedding = embedding or _UNKNOWN_EMBEDDING_LENGTH
    ubatch = max(int(ubatch or LLAMA_DEFAULT_UBATCH), 1)
    # The CUDA compute buffer is dominated by one ubatch of f32 logits plus
    # the matching hidden state: 512 x (248320 + 4096) x 4 B = 493 MiB for
    # Qwen3.5 9B, exactly what b9014 reports (246.5 MiB at ubatch 256).
    # Pipeline-parallel multi-GPU splits keep several copies per device.
    compute_mib = ubatch * 4.0 * (vocab + embedding) / MIB
    if gpu_count > 1:
        compute_mib *= 4.0 * gpu_count

    total_mib = weights_mib + kv_mib + recurrent_mib + compute_mib
    return {
        "basis": basis,
        "contextLength": context,
        "cacheTypeK": cache_k,
        "cacheTypeV": cache_v,
        "ubatch": ubatch,
        "weightsMiB": round(weights_mib, 2),
        "kvMiB": round(kv_mib, 2),
        "recurrentMiB": round(recurrent_mib, 2),
        "computeMiB": round(compute_mib, 2),
        "totalMiB": round(total_mib, 2),
    }


def _round_up_gb(mib: float) -> float:
    return math.ceil((mib / 1024.0) * 100.0 - 1e-9) / 100.0


def gpu_residency_fit(
    model: dict[str, Any],
    *,
    total_vram_mb: float,
    context_length: int | None = None,
    runtime_profile: dict[str, Any] | None = None,
    env: dict[str, Any] | None = None,
    gpu_platform: str | None = None,
    gpu_count: int = 1,
    other_used_mib: float = 0.0,
    weight_size_mb: int | float | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide whether a model is fully GPU-resident on a discrete GPU.

    Fits when llama.cpp's projection is at most what the device leaves free
    after the platform reserve, memory already held by other processes, and
    the fit target llama.cpp keeps free. ``requiredGb`` is the total device
    memory this configuration needs on this platform, directly comparable
    with the GPU's reported total.
    """
    settings = runtime_memory_settings(runtime_profile, env, overrides)
    if context_length is None:
        if isinstance(runtime_profile, dict) and runtime_profile.get("context_length"):
            context_length = int(runtime_profile["context_length"])
        else:
            context_length = int(model.get("context_length") or 0) or None
    gpu_count = max(int(gpu_count or 1), 1)
    kind = (gpu_platform or detect_gpu_platform()).lower()
    total = _positive_number(total_vram_mb)
    projection = estimated_device_memory_mib(
        model,
        context_length=context_length,
        cache_type_k=settings["cacheTypeK"],
        cache_type_v=settings["cacheTypeV"],
        ubatch=settings["ubatch"],
        parallel=settings["parallel"],
        gpu_count=gpu_count,
        weight_size_mb=weight_size_mb,
    )
    exact = projection["basis"] in {"measured", "gguf"}
    profile_gb = (
        _positive_number(runtime_profile.get("estimated_required_gb"))
        if isinstance(runtime_profile, dict)
        else 0.0
    )
    profile_context = (
        int(_positive_number(runtime_profile.get("context_length")))
        if isinstance(runtime_profile, dict)
        else 0
    )
    if (
        not exact
        and profile_gb
        and not overrides
        and (not profile_context or profile_context == projection["contextLength"])
    ):
        # Without exact metadata, a runtime profile's number is the device
        # projection for its own settings (catalog contract: never below
        # llama.cpp's projection).
        projection = {**projection, "basis": "profile", "totalMiB": round(profile_gb * 1024.0, 2)}
    reserve_mib = platform_reserve_mib(total / gpu_count, kind) * gpu_count
    fit_target_mib = settings["fitTargetMiB"] * gpu_count
    other_mib = max(_positive_number(other_used_mib), 0.0)
    available_mib = total - reserve_mib - other_mib
    budget_mib = available_mib - fit_target_mib
    required_total_mib = projection["totalMiB"] + reserve_mib + fit_target_mib + other_mib
    # Catalog entries without exact metadata keep their declared VRAM class
    # ("needs an 8GB GPU") as an additional floor on the GPU's total memory.
    # A hardware-matched runtime profile is the contract for this GPU class
    # (for example a declared MoE expert offload), so it replaces that floor.
    declared_class_gb = (
        0.0
        if exact or projection["basis"] == "profile"
        else _positive_number(model.get("vram_required_gb"))
    )
    declared_ok = declared_class_gb <= (total / 1024.0) + DECLARED_VRAM_CLASS_TOLERANCE_GB
    required_gb = max(_round_up_gb(required_total_mib), round(declared_class_gb, 2))
    return {
        "fits": bool(total > 0 and projection["totalMiB"] <= budget_mib and declared_ok),
        "platform": kind,
        "gpuCount": gpu_count,
        "totalMiB": round(total, 2),
        "reserveMiB": reserve_mib,
        "otherUsedMiB": round(other_mib, 2),
        "fitTargetMiB": fit_target_mib,
        "availableMiB": round(available_mib, 2),
        "budgetMiB": round(budget_mib, 2),
        "headroomMiB": round(budget_mib - projection["totalMiB"], 2),
        "requiredGb": required_gb,
        "declaredClassGb": declared_class_gb or None,
        "projection": projection,
        "settings": settings,
    }


def _kv_fallback_types(cache_type_k: str, cache_type_v: str) -> list[str]:
    """Next KV cache types to try, one step at a time."""
    current = max(kv_cache_type_bytes(cache_type_k), kv_cache_type_bytes(cache_type_v))
    return [
        value for value in KV_CACHE_FALLBACK_LADDER
        if KV_CACHE_TYPE_BYTES[value] < current - 1e-9
    ]


def plan_residency_fallback(
    *,
    required_mib: float,
    available_mib: float,
    settings: dict[str, Any],
    kv_mib: float,
    compute_mib: float,
    context_length: int,
    allow_context_reduction: bool = True,
    context_floor: int = RESIDENCY_CONTEXT_FLOOR,
    guard_mib: float = RESIDENCY_PLAN_GUARD_MIB,
    gpu_count: int = 1,
    locked_keys: Iterable[str] = (),
    best_effort: bool = False,
    late_steps: bool = True,
) -> dict[str, Any] | None:
    """Return the smallest settings change expected to make full offload fit.

    ``required_mib`` is llama.cpp's full-offload projection for the current
    settings and ``available_mib`` the device memory it sees free before
    loading (read from the load log, or ``total - reserve - other`` when
    planning before launch), both summed over ``gpu_count`` devices; the fit
    target is kept free on every device. Steps, in order, stop at the first
    that fits:

    1. ubatch 256 (benchmarked: identical output, 4-6% slower prefill);
    2. fit target 512 MiB (benchmarked: still covers the ~256 MiB the
       flash-attention pool grows by at 64K);
    3. a q8_0 KV cache;
    4. a smaller standard context, never below the agent floor, never below
       a context that was already under it, and never for an explicitly
       requested context;
    5. ubatch 128 (the compute buffer halves again; prompt processing is
       slower, the output is not quantized further; not benchmarked);
    6. a q4_0 KV cache (benchmarked as a fallback: output diverges from
       q8_0 but passed the edit and needle checks).

    With ``late_steps`` False the ladder stops after step 4: a caller that
    walks contexts itself (model_selection's ranker) tries every context
    down to the agent floor with steps 1-3 before it accepts ubatch 128 or a
    q4_0 KV cache, the same order as step 4 here.

    Keys in ``locked_keys`` (an operator's own ``LLAMA_ARG_UBATCH`` or
    ``LLAMA_ARG_FIT_TARGET``) are never changed. The result carries
    ``fits``. When nothing fits it returns ``None``, or with ``best_effort``
    the most memory-saving allowed configuration with ``fits`` False, so a
    caller that must load anyway (memory held by other processes) keeps as
    many layers on the GPU as it can and reports the rest.
    """
    locked = {str(key) for key in locked_keys}
    ubatch = int(settings.get("ubatch") or LLAMA_DEFAULT_UBATCH)
    fit_target = int(settings.get("fitTargetMiB") or LLAMA_DEFAULT_FIT_TARGET_MIB)
    cache_k = str(settings.get("cacheTypeK") or "f16").lower()
    cache_v = str(settings.get("cacheTypeV") or "f16").lower()
    context = int(context_length)
    need = float(required_mib)
    kv = max(float(kv_mib), 0.0)
    compute = max(float(compute_mib), 0.0)
    changes: dict[str, str] = {}
    steps: list[str] = []

    devices = max(int(gpu_count or 1), 1)

    def fits() -> bool:
        return need + guard_mib <= float(available_mib) - fit_target * devices

    def result(fitted: bool = True) -> dict[str, Any]:
        return {
            "fits": fitted,
            "changes": dict(changes),
            "steps": list(steps),
            "projectedMiB": round(need, 2),
            "availableMiB": round(float(available_mib), 2),
            "fitTargetMiB": fit_target,
            "contextLength": context,
            "cacheTypeK": cache_k,
            "cacheTypeV": cache_v,
            "ubatch": ubatch,
        }

    def shrink_ubatch(target: int) -> None:
        nonlocal need, compute, ubatch
        saved = compute * (1.0 - target / ubatch)
        need -= saved
        compute -= saved
        ubatch = target
        changes["LLAMA_ARG_UBATCH"] = str(target)
        steps.append(f"ubatch {target}")

    if fits():
        return result()

    if ubatch > RESIDENCY_FALLBACK_UBATCH and "LLAMA_ARG_UBATCH" not in locked:
        shrink_ubatch(RESIDENCY_FALLBACK_UBATCH)
        if fits():
            return result()
    if fit_target > MIN_SAFE_FIT_TARGET_MIB and "LLAMA_ARG_FIT_TARGET" not in locked:
        fit_target = MIN_SAFE_FIT_TARGET_MIB
        changes["LLAMA_ARG_FIT_TARGET"] = str(fit_target)
        steps.append(f"fit target {MIN_SAFE_FIT_TARGET_MIB} MiB")
        if fits():
            return result()

    def quantize_kv(next_type: str) -> None:
        nonlocal need, kv, cache_k, cache_v
        before = kv_cache_type_bytes(cache_k) + kv_cache_type_bytes(cache_v)
        new_kv = kv * (2.0 * KV_CACHE_TYPE_BYTES[next_type]) / before if before else kv
        need -= kv - new_kv
        kv = new_kv
        cache_k = cache_v = next_type
        changes["LLAMA_ARG_CACHE_TYPE_K"] = next_type
        changes["LLAMA_ARG_CACHE_TYPE_V"] = next_type
        # A quantized V cache requires flash attention in llama.cpp.
        changes["LLAMA_ARG_FLASH_ATTN"] = "on"
        steps.append(f"KV cache {next_type}")

    kv_steps = _kv_fallback_types(cache_k, cache_v)
    # q8_0 first: it halves f16 KV with negligible quality loss.
    if kv_steps and kv_steps[0] == KV_CACHE_FALLBACK_LADDER[0]:
        quantize_kv(kv_steps.pop(0))
        if fits():
            return result()

    # Then give back context above the agent floor before accepting q4_0.
    if allow_context_reduction:
        floor = min(int(context_floor or 0), context)
        for candidate in sorted(STANDARD_CONTEXT_LENGTHS, reverse=True):
            if candidate >= context or candidate < floor:
                continue
            new_kv = kv * candidate / context
            need -= kv - new_kv
            kv = new_kv
            context = candidate
            changes["CTX_SIZE"] = str(candidate)
            changes["MAX_CONTEXT"] = str(candidate)
            steps.append(f"context {candidate}")
            if fits():
                return result()

    if not late_steps:
        return result(False) if best_effort and changes else None

    # ubatch 128 only slows prompt processing; q4_0 changes the output.
    ubatch_before_min = ubatch
    if ubatch > RESIDENCY_MIN_UBATCH and "LLAMA_ARG_UBATCH" not in locked:
        shrink_ubatch(RESIDENCY_MIN_UBATCH)
        if fits():
            return result()

    for next_type in kv_steps:
        quantize_kv(next_type)
        if fits():
            if ubatch < ubatch_before_min:
                # The quantized cache alone may be enough: keep the faster
                # ubatch when it still fits.
                restored = compute * (ubatch_before_min / ubatch - 1.0)
                if need + restored + guard_mib <= float(available_mib) - fit_target * devices:
                    need += restored
                    compute += restored
                    steps.remove(f"ubatch {ubatch}")
                    ubatch = ubatch_before_min
                    if ubatch == int(settings.get("ubatch") or LLAMA_DEFAULT_UBATCH):
                        changes.pop("LLAMA_ARG_UBATCH", None)
                    else:
                        changes["LLAMA_ARG_UBATCH"] = str(ubatch)
            return result()
    return result(False) if best_effort and changes else None


def resident_configuration(
    model: dict[str, Any],
    *,
    total_vram_mb: float,
    runtime_profile: dict[str, Any] | None = None,
    env: dict[str, Any] | None = None,
    context_length: int | None = None,
    gpu_platform: str | None = None,
    gpu_count: int = 1,
    other_used_mib: float = 0.0,
    weight_size_mb: int | float | None = None,
    allow_context_reduction: bool = True,
    pinned: dict[str, Any] | None = None,
    late_steps: bool = True,
) -> dict[str, Any]:
    """Choose the configuration that keeps a model fully GPU-resident.

    This is the single residency entry point for the installer selector,
    the dashboard and activation (see the section comment above). Order: the
    configuration as declared; then ``plan_residency_fallback`` (ubatch 256,
    fit target 512, q8_0 KV, context down to the agent floor, ubatch 128,
    q4_0 KV) for
    models whose memory is known exactly (measured or GGUF-derived
    ``gpu_residency``) because the planner works with thin margins; then,
    for models with only architecture metadata, the largest standard context
    at the declared settings. Context is never traded below the agent floor,
    or below a configured context that is already under it.

    ``other_used_mib`` is GPU memory held by other processes (a desktop,
    Whisper, ComfyUI); ``gpu_count`` devices each keep their own reserve and
    fit target. ``pinned`` holds an operator's own ``LLAMA_ARG_UBATCH`` /
    ``LLAMA_ARG_FIT_TARGET``: they win over the runtime profile and the
    planner never changes them.

    Returns:

    - ``fits``: every layer, the KV cache and the compute buffers stay on the
      GPU with ``overrides`` applied at ``contextLength``;
    - ``overrides``: llama.cpp env values to launch with (``pinned`` excluded)
      and ``steps``, the same changes in words;
    - ``residency``: the ``gpu_residency_fit`` record of the chosen (or, when
      nothing fits, the declared) configuration;
    - ``idleFits``: whether it would fit with nothing else holding GPU memory.
      False means the model is too large for this GPU; True with ``fits``
      False means other processes hold the memory it needs;
    - ``bestEffort``: when nothing fits and the memory is known exactly, the
      most memory-saving allowed configuration (``overrides``, ``steps``,
      ``contextLength``, ``residency``) for callers that load anyway and
      report the placement; otherwise None.
    """
    pinned_env = {
        str(key): str(value)
        for key, value in (pinned or {}).items()
        if value is not None and str(value).strip() != ""
    }
    kwargs: dict[str, Any] = {
        "total_vram_mb": total_vram_mb,
        "runtime_profile": runtime_profile,
        "env": env,
        "gpu_platform": gpu_platform,
        "gpu_count": gpu_count,
        "weight_size_mb": weight_size_mb,
    }
    if context_length is None:
        if isinstance(runtime_profile, dict) and runtime_profile.get("context_length"):
            context_length = int(runtime_profile["context_length"])
        else:
            context_length = int(_positive_number(model.get("context_length"))) or None

    def configure(other_mib: float, *, best_effort: bool) -> dict[str, Any]:
        declared = gpu_residency_fit(
            model, context_length=context_length, other_used_mib=other_mib,
            overrides=pinned_env or None, **kwargs,
        )
        if declared["fits"]:
            return {
                "fits": True, "residency": declared, "overrides": {}, "steps": [],
                "contextLength": declared["projection"]["contextLength"], "bestEffort": None,
            }
        projection = declared["projection"]
        context = projection["contextLength"]
        exact = projection["basis"] in {"measured", "gguf"}
        plan = None if not exact else plan_residency_fallback(
            required_mib=projection["totalMiB"],
            available_mib=declared["availableMiB"],
            settings=declared["settings"],
            kv_mib=projection["kvMiB"],
            compute_mib=projection["computeMiB"],
            context_length=context,
            allow_context_reduction=allow_context_reduction,
            context_floor=min(RESIDENCY_CONTEXT_FLOOR, context),
            gpu_count=gpu_count,
            locked_keys=pinned_env,
            best_effort=best_effort,
            late_steps=late_steps,
        )
        best = None
        if plan is not None:
            overrides = {
                key: value for key, value in plan["changes"].items()
                if key.startswith("LLAMA_ARG_")
            }
            planned = gpu_residency_fit(
                model, context_length=plan["contextLength"], other_used_mib=other_mib,
                overrides={**overrides, **pinned_env}, **kwargs,
            )
            if plan["fits"] and planned["fits"]:
                return {
                    "fits": True, "residency": planned, "overrides": overrides,
                    "steps": plan["steps"], "contextLength": plan["contextLength"],
                    "bestEffort": None,
                }
            best = {
                "overrides": overrides, "steps": list(plan["steps"]),
                "contextLength": plan["contextLength"], "residency": planned,
            }

        if allow_context_reduction and not exact and _positive_number(model.get("block_count")):
            floor = min(RESIDENCY_CONTEXT_FLOOR, context)
            for candidate in sorted(STANDARD_CONTEXT_LENGTHS, reverse=True):
                if candidate >= context or candidate < floor:
                    continue
                reduced = gpu_residency_fit(
                    model, context_length=candidate, other_used_mib=other_mib,
                    overrides=pinned_env or None, **kwargs,
                )
                if reduced["fits"]:
                    return {
                        "fits": True, "residency": reduced, "overrides": {},
                        "steps": [f"context {candidate}"], "contextLength": candidate,
                        "bestEffort": None,
                    }
        return {
            "fits": False, "residency": declared, "overrides": {}, "steps": [],
            "contextLength": context, "bestEffort": best,
        }

    other = max(_positive_number(other_used_mib), 0.0)
    config = configure(other, best_effort=True)
    config["idleFits"] = bool(
        config["fits"] or (other > 0 and configure(0.0, best_effort=False)["fits"])
    )
    return config


# ---------------------------------------------------------------------------
# Observed placement (llama.cpp load log)
# ---------------------------------------------------------------------------

PLACEMENT_SCHEMA = "ods.model-placement.v1"

_LOADER_RE = re.compile(r"llama_model_loader: loaded meta data with .* from (?P<path>.+?)\s*(?:\(version .*\))?\s*$")
_OFFLOADED_RE = re.compile(r"offloaded (?P<on>\d+)/(?P<total>\d+) layers to GPU")
_MODEL_BUFFER_RE = re.compile(r"load_tensors:\s+(?P<device>\S+) model buffer size =\s+(?P<mib>[\d.]+) MiB")
_KV_BUFFER_RE = re.compile(r"\s(?P<device>\S+) KV buffer size =\s+(?P<mib>[\d.]+) MiB")
_RS_BUFFER_RE = re.compile(r"\s(?P<device>\S+) RS buffer size =\s+(?P<mib>[\d.]+) MiB")
_COMPUTE_BUFFER_RE = re.compile(r"\s(?P<device>\S+) compute buffer size =\s+(?P<mib>[\d.]+) MiB")
_PROJECTION_RE = re.compile(
    r"projected to use (?P<need>\d+) MiB of device memory vs\. (?P<free>\d+) MiB of free device memory"
)
_TARGET_UNMET_RE = re.compile(r"cannot meet free memory target of (?P<target>\d+) MiB")
_TARGET_MET_RE = re.compile(r"will leave -?\d+ >= (?P<target>\d+) MiB of free device memory")
# With several GPUs llama.cpp prints one line per device instead
# (common/fit.cpp: "- CUDA0 (...): 97246 total, 16631 used, 79667 free vs.
# target of 1024"); the projection line is then the sum over devices.
_TARGET_DEVICE_RE = re.compile(
    r"params_fit_impl:\s+- .+?:\s+-?\d+ total,\s+-?\d+ used,\s+-?\d+ free vs\. target of\s+(?P<target>\d+)"
)
# The fit plan per device, printed in device order (common/fit.cpp). For MoE
# models llama.cpp can keep a layer "on the GPU" while moving part of it off
# the device: "- CUDA0 (...): 49 layers (12 overflowing), ...". The load log
# still says every layer is offloaded, so the overflow count is the signal.
# All but one overflowing layer per device put their expert weights in system
# memory. The first one goes to the next GPU when llama.cpp logged
# "set ngl_per_device[i].(n_layer, n_part, overflow_type)=(.., .., UP|GATE|ATTN)"
# for a device that is not the last; on the last device it goes to system
# memory too.
_FIT_DEVICE_RE = re.compile(
    r"params_fit_impl:\s+- .+?: +\d+ layers \( *(?P<overflow>\d+) overflowing\)"
)
_FIT_NEXT_DEVICE_RE = re.compile(
    r"set ngl_per_device\[(?P<id>\d+)\]\.\(n_layer, n_part, overflow_type\)=\(\s*\d+,\s*\d+,\s*(?:UP|GATE|ATTN)\)"
)
_VOCAB_RE = re.compile(r"print_info: n_vocab\s+=\s+(?P<value>\d+)")
_EMBD_RE = re.compile(r"print_info: n_embd\s+=\s+(?P<value>\d+)\s*$")


# Substrings of every log line parse_llama_placement reads.
_PLACEMENT_LINE_MARKERS = (
    "loaded meta data with",
    "layers to GPU",
    "buffer size =",
    "projected to use",
    "free memory target",
    "of free device memory",
    "vs. target of",
    "overflowing)",
    "overflow_type)=(",
    "print_info: n_vocab",
    "print_info: n_embd",
)
# llama-server logs one of these once the model is loaded and serving.
_READY_LINE_MARKERS = (
    "main: model loaded",
    "server is listening on",
    "all slots are idle",
)


def is_llama_start_line(line: str) -> bool:
    """True for the line llama-server logs once per start, before loading."""
    return "main: loading model" in line


def is_placement_log_line(line: str) -> bool:
    """True for llama.cpp log lines that describe model placement."""
    return any(marker in line for marker in _PLACEMENT_LINE_MARKERS)


def is_llama_ready_line(line: str) -> bool:
    """True once llama-server reports the model loaded and serving."""
    return any(marker in line for marker in _READY_LINE_MARKERS)


def _host_buffer(device: str) -> bool:
    name = device.strip().lower()
    return name.startswith("cpu") or name.endswith("_host") or name == "host"


def _block_for_model(lines: list[str], expected_model_file: str | None) -> tuple[list[str], str]:
    """Return the log lines for the most recent load of the expected model."""
    loader_indexes = [index for index, line in enumerate(lines) if _LOADER_RE.search(line)]
    target_index = None
    expected = os.path.basename(str(expected_model_file or "")).lower()
    for index in reversed(loader_indexes):
        path = _LOADER_RE.search(lines[index]).group("path")
        if not expected or os.path.basename(path.strip()).lower() == expected:
            target_index = index
            break
    if target_index is None:
        if loader_indexes and expected:
            return [], ""
        offloaded = [index for index, line in enumerate(lines) if _OFFLOADED_RE.search(line)]
        if not offloaded:
            return [], ""
        start = offloaded[-1]
        previous = [index for index in offloaded if index < start]
        begin = previous[-1] + 1 if previous else 0
        return lines[begin:], ""
    # llama.cpp logs its fit projection before the real load: start after the
    # previous load's last line so a restart's projection is not reused.
    previous_loads = [index for index in loader_indexes if index < target_index]
    begin = 0
    if previous_loads:
        begin = previous_loads[-1] + 1
        for index in range(target_index - 1, previous_loads[-1], -1):
            if _OFFLOADED_RE.search(lines[index]) or "compute buffer size" in lines[index]:
                begin = index + 1
                break
    next_loads = [index for index in loader_indexes if index > target_index]
    end = next_loads[0] if next_loads else len(lines)
    path = _LOADER_RE.search(lines[target_index]).group("path").strip()
    return lines[begin:end], os.path.basename(path)


def parse_llama_placement(
    log_text: str,
    *,
    expected_model_file: str | None = None,
    intentional_offload: bool = False,
) -> dict[str, Any]:
    """Read where llama.cpp actually put a model from its load log.

    ``fullyResident`` means every repeating layer and the output layer are on
    the GPU and no KV cache or recurrent state lives in host memory. The token
    embedding is always host-resident in llama.cpp, so ``cpuWeightMiB`` is
    non-zero even for a fully resident model and is informational only. MoE
    expert tensors kept on the CPU by ``--n-cpu-moe``/``--cpu-moe``/``-ot``
    are an allowed exception only when the caller declares the offload
    intentional (catalog/tier runtime profile).
    """
    lines = str(log_text or "").splitlines()
    block, model_file = _block_for_model(lines, expected_model_file)
    base: dict[str, Any] = {
        "schema": PLACEMENT_SCHEMA,
        "source": "llama-server-log",
        "modelFile": model_file or (os.path.basename(expected_model_file) if expected_model_file else None),
        "layersOnGpu": None,
        "layersTotal": None,
        "cpuWeightMiB": None,
        "gpuWeightMiB": None,
        "cpuKvMiB": None,
        "gpuKvMiB": None,
        "gpuComputeMiB": None,
        "projectedDeviceMiB": None,
        "freeDeviceMiB": None,
        "fitTargetMiB": None,
        "vocabSize": None,
        "embeddingLength": None,
        "fullyResident": None,
        "intentionalOffload": bool(intentional_offload),
        "status": "unverified",
        "reason": "",
    }
    offloaded = None
    for line in block:
        match = _OFFLOADED_RE.search(line)
        if match and offloaded is None:
            offloaded = (int(match.group("on")), int(match.group("total")))
    if offloaded is None:
        base["reason"] = (
            "The runtime log does not report layer placement for this model "
            "(llama.cpp build or runtime does not print it)."
        )
        return base

    def total(regex: re.Pattern[str], host: bool | None) -> float:
        value = 0.0
        for line in block:
            match = regex.search(line)
            if not match:
                continue
            is_host = _host_buffer(match.group("device"))
            if host is None or host == is_host:
                value += float(match.group("mib"))
        return round(value, 2)

    projection = next((m for m in map(_PROJECTION_RE.search, block) if m), None)
    device_targets = [int(m.group("target")) for m in map(_TARGET_DEVICE_RE.search, block) if m]
    target_match = next(
        (m for m in (_TARGET_UNMET_RE.search(l) or _TARGET_MET_RE.search(l) for l in block) if m),
        None,
    )
    fit_target = (
        int(target_match.group("target")) if target_match
        else max(device_targets) if device_targets
        else None
    )
    vocab = next((m for m in map(_VOCAB_RE.search, block) if m), None)
    embd = next((m for m in map(_EMBD_RE.search, block) if m), None)
    device_plan = [int(m.group("overflow")) for m in map(_FIT_DEVICE_RE.search, block) if m]
    to_next_gpu = {int(m.group("id")) for m in map(_FIT_NEXT_DEVICE_RE.search, block) if m}
    device_count = max(len(device_targets), len(device_plan), 1)
    overflowing = 0
    next_gpu_overflow = 0
    for index, count in enumerate(device_plan):
        spills_to_next = 1 if count and index in to_next_gpu and index < len(device_plan) - 1 else 0
        next_gpu_overflow += spills_to_next
        overflowing += count - spills_to_next
    on_gpu, layers_total = offloaded
    cpu_kv = total(_KV_BUFFER_RE, True) + total(_RS_BUFFER_RE, True)
    base.update({
        "layersOnGpu": on_gpu,
        "layersTotal": layers_total,
        "cpuWeightMiB": total(_MODEL_BUFFER_RE, True),
        "gpuWeightMiB": total(_MODEL_BUFFER_RE, False),
        "cpuKvMiB": round(cpu_kv, 2),
        "gpuKvMiB": round(total(_KV_BUFFER_RE, False) + total(_RS_BUFFER_RE, False), 2),
        "kvMiB": total(_KV_BUFFER_RE, None),
        "gpuComputeMiB": total(_COMPUTE_BUFFER_RE, False),
        "projectedDeviceMiB": int(projection.group("need")) if projection else None,
        "freeDeviceMiB": int(projection.group("free")) if projection else None,
        "fitTargetMiB": fit_target,
        "deviceCount": device_count,
        "vocabSize": int(vocab.group("value")) if vocab else None,
        "embeddingLength": int(embd.group("value")) if embd else None,
        # Layers whose MoE expert weights llama.cpp's fit moved to system
        # memory; a split to the next GPU stays on the GPU and is only noted.
        "overflowingLayers": overflowing,
        "nextGpuOverflowLayers": next_gpu_overflow,
    })
    layers_resident = layers_total > 0 and on_gpu >= layers_total
    fully = bool(layers_resident and cpu_kv <= 0.0 and overflowing == 0)
    base["fullyResident"] = fully
    if layers_total > 0 and on_gpu == 0:
        base["status"] = "cpu_only"
        base["reason"] = "No model layers were placed on the GPU."
    elif fully and intentional_offload:
        base["status"] = "intentional_offload"
        base["reason"] = (
            "All layers are on the GPU; MoE expert weights are kept in system "
            "memory by the model's declared runtime profile."
        )
    elif fully:
        base["status"] = "fully_resident"
    else:
        base["status"] = "partial"
        detail = f"{on_gpu}/{layers_total} layers on the GPU"
        if overflowing:
            detail += f", MoE expert weights of {overflowing} layers moved to system memory by llama.cpp's fit"
        if cpu_kv > 0:
            detail += f", {cpu_kv:g} MiB of KV cache in system memory"
        if projection and fit_target is not None:
            margin = (
                f"a {fit_target} MiB margin"
                if device_count == 1
                else f"a {fit_target} MiB margin on each of {device_count} GPUs"
            )
            detail += (
                f"; llama.cpp projected {projection.group('need')} MiB against "
                f"{projection.group('free')} MiB free with {margin}"
            )
        base["reason"] = detail
    return base


# ---------------------------------------------------------------------------
# CPU threads for intentional offload
# ---------------------------------------------------------------------------


def _parse_cpu_list(text: str) -> list[int]:
    cpus: list[int] = []
    for part in str(text or "").strip().split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            try:
                cpus.extend(range(int(start), int(end) + 1))
            except ValueError:
                return []
        else:
            try:
                cpus.append(int(part))
            except ValueError:
                return []
    return cpus


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeError):
        return ""


def _physical_core_ids(cpus: list[int], sysfs_root: str) -> set[tuple[str, str]]:
    cores: set[tuple[str, str]] = set()
    for cpu in cpus:
        base = os.path.join(sysfs_root, "devices", "system", "cpu", f"cpu{cpu}", "topology")
        package = _read_text(os.path.join(base, "physical_package_id")).strip()
        core = _read_text(os.path.join(base, "core_id")).strip()
        cores.add((package or "0", core or str(cpu)))
    return cores


# Intel families with performance and efficiency cores (12th-14th gen Core,
# Core Ultra). Used only when the OS hides the P/E split.
_INTEL_HYBRID_MODEL_RE = re.compile(
    r"Intel\(R\) Core\(TM\) Ultra|Intel\(R\) Core\(TM\) i[3579]-1[234]\d{2,3}|"
    r"1[234]th Gen Intel\(R\) Core",
    re.IGNORECASE,
)
# Desktop parts of those generations with performance cores only: every i3
# x100 (4 P-cores) and the i5-12400/12490/12500/12600 without a K or H
# suffix (6 P-cores). Their cores are all performance cores.
_INTEL_PERFORMANCE_ONLY_MODEL_RE = re.compile(
    r"i3-1[234]1\d0[FT]?\b|i5-12[456]\d0[FT]?\b",
    re.IGNORECASE,
)


def performance_core_count(
    *,
    sysfs_root: str = "/sys",
    cpuinfo_path: str = "/proc/cpuinfo",
    system: str | None = None,
) -> int:
    """Physical performance cores to use for CPU-side llama.cpp work.

    Used only when a runtime profile intentionally keeps work on the CPU (MoE
    expert offload). The compose default of 4 threads predates ODS and
    ignores the host; every logical core is worse on hybrid CPUs: on the
    Core Ultra 9 285H under WSL, 6 threads decoded 10% faster than 4 while 14
    threads decoded 64% slower (perf/laptop-gpu-residency-bench.md, V5a-c).
    Returns 0 when the count cannot be determined.
    """
    kind = (system or sys.platform).lower()
    if kind == "darwin":
        import subprocess

        for key in ("hw.perflevel0.physicalcpu", "hw.physicalcpu"):
            try:
                result = subprocess.run(
                    ["sysctl", "-n", key], capture_output=True, text=True, timeout=5,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            value = _positive_whole(result.stdout.strip()) if result.returncode == 0 else 0
            if value:
                return value
        return 0
    if not kind.startswith("linux"):
        count = os.cpu_count() or 0
        return max(count // 2, 1) if count else 0

    # Intel hybrid on native Linux exposes the P-core set directly.
    p_cores = _parse_cpu_list(_read_text(os.path.join(sysfs_root, "devices", "cpu_core", "cpus")))
    if p_cores:
        return len(_physical_core_ids(p_cores, sysfs_root))

    cpu_root = os.path.join(sysfs_root, "devices", "system", "cpu")
    online = _parse_cpu_list(_read_text(os.path.join(cpu_root, "online")))
    if not online:
        count = os.cpu_count() or 0
        online = list(range(count))
    if not online:
        return 0

    # ARM big.LITTLE: keep the highest-capacity cluster.
    capacities = {
        cpu: _positive_whole(_read_text(os.path.join(cpu_root, f"cpu{cpu}", "cpu_capacity")))
        for cpu in online
    }
    if any(capacities.values()) and len(set(capacities.values())) > 1:
        top = max(capacities.values())
        return len(_physical_core_ids([cpu for cpu, cap in capacities.items() if cap == top], sysfs_root))

    physical = len(_physical_core_ids(online, sysfs_root))
    flags = ""
    model_name = ""
    for line in _read_text(cpuinfo_path).splitlines():
        lowered = line.lower()
        if not flags and lowered.startswith("flags"):
            flags = line
        elif not model_name and lowered.startswith("model name"):
            model_name = line.partition(":")[2].strip()
    if "hybrid_cpu" in flags.split() or (
        _INTEL_HYBRID_MODEL_RE.search(model_name)
        and not _INTEL_PERFORMANCE_ONLY_MODEL_RE.search(model_name)
    ):
        # A hybrid CPU whose P/E split is hidden. WSL exposes the Core Ultra 9
        # 285H as 16 identical cores with no hybrid flag or cpu_core set
        # (checked on windows-laptop-wsl-beta, 2026-09-25). Current Intel
        # mobile parts have 6 P-cores of 16 (Core Ultra 9 285H, 7 155H);
        # 3/8 of the cores approximates that without reaching the E-cores that
        # slowed decode in the benchmark.
        return max(physical * 3 // 8, 1)
    return physical
