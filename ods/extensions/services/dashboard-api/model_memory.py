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

The GPU-residency work reuses :func:`estimate_model_memory`,
:class:`MemoryEstimate` and :func:`fit_margin_gib`; do not re-derive KV there.
"""

from __future__ import annotations

import math
import re
from typing import Any, NamedTuple


MEMORY_METADATA_KEYS = (
    "total_params_b", "params_b", "block_count", "embedding_length",
    "attention_head_count", "head_count", "attention_head_count_kv",
    "head_count_kv", "attention_head_dimension", "head_dimension",
    "attention_key_length", "attention_value_length", "kv_cache_element_bytes",
    "attention_layer_count", "full_attention_interval", "recurrent_state_bytes",
    "size_bytes", "max_context_length",
    "sliding_window", "sliding_window_layer_count", "sliding_window_head_count_kv",
    "sliding_window_key_length", "sliding_window_value_length",
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
# A discrete card also drives the display and the CUDA context of other
# processes; the architecture estimate must leave this much free. The
# GPU-residency work owns tuning these two values.
DISCRETE_FIT_MARGIN_MIN_GIB = 0.25
DISCRETE_FIT_MARGIN_FRACTION = 0.03
# Legacy and authored (runtime-profile) estimates keep the historical
# tolerance for GPUs that report slightly under their marketed size.
LEGACY_FIT_TOLERANCE_GIB = 0.25
MINIMUM_CONTEXT = 8192
CONTEXT_STEPS = (8192, 16384, 32768, 65536, 131072, 262144)

MEMORY_CLASSES = ("discrete", "unified", "cpu")


def memory_metadata(model: dict[str, Any]) -> dict[str, Any]:
    """Preserve architecture inputs when normalizing catalog records."""
    return {key: model[key] for key in MEMORY_METADATA_KEYS if key in model}


def with_file_metadata(model: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """``model`` with a GGUF header's values (gguf_inspector) layered on top.

    The header fills in what the catalog does not declare. A reviewed catalog
    layout (:func:`architecture_metadata_complete`) stays authoritative for
    the estimator's inputs: a GGUF header does not describe sliding-window or
    shared-KV layers the way the estimator reads them (a Gemma 3 header names
    no sliding layers at all; a Gemma 4 header lists KV heads for every layer,
    sliding ones included), so letting it override the reviewed keys charges
    full-attention KV on every layer and a model that fits looks too large.
    """
    merged = {**model, **metadata}
    if architecture_metadata_complete(model):
        merged.update(memory_metadata(model))
    return merged


def declared_max_context(model: dict[str, Any]) -> int:
    """The catalog's declared native maximum context, or 0 when undeclared.

    ``max_context_length`` is the model's native context: the GGUF
    ``<arch>.context_length`` (``n_ctx_train``), above which llama.cpp caps
    the slot, or a lower documented maximum
    (tests/test_model_library_native_context.py keeps it at or below the
    header). The dashboard's normalized entries
    (performance_oracle.normalize_catalog_entry) fill ``max_context_length``
    from ``context_length`` when the entry declares none and mark that with
    ``native_context_declared: False``; such an entry has no known ceiling, so
    an owner's larger context is not clamped to the catalog default.

    Lives here, not in model_selection, so bin/ods-host-agent.py (which loads
    only this file) applies the same ceiling; model_selection re-exports it.
    """
    if model.get("native_context_declared") is False:
        return 0
    value = model.get("max_context_length")
    if isinstance(value, bool):
        return 0
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


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


def fit_margin_gib(capacity_gib: float, memory_class: str) -> float:
    """Free memory an architecture estimate must leave on this memory class.

    Discrete GPUs keep ``max(0.25 GiB, 3%)`` for the display and other CUDA
    contexts. Unified memory already uses a 55% share of RAM, and the CPU
    capacity is already a bounded share of RAM, so neither adds a margin.
    """
    if memory_class != "discrete":
        return 0.0
    capacity = _positive_number(capacity_gib)
    return round(max(DISCRETE_FIT_MARGIN_MIN_GIB, DISCRETE_FIT_MARGIN_FRACTION * capacity), 2)


def memory_fits(
    required_gib: float,
    capacity_gib: float,
    memory_class: str,
    *,
    architecture_estimate: bool,
) -> bool:
    """Apply the fit rule for one requirement.

    Architecture estimates must leave :func:`fit_margin_gib` free. Legacy and
    authored (runtime-profile) estimates keep the historical +0.25 GiB
    tolerance so validated profiles behave exactly as before.
    """
    if architecture_estimate:
        return required_gib <= capacity_gib - fit_margin_gib(capacity_gib, memory_class) + 1e-9
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
