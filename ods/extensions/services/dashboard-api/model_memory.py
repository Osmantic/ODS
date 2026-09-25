"""Shared context-aware memory estimates for model selection and activation."""

from __future__ import annotations

import math
import os
import platform as _platform
import re
import sys
from typing import Any


MEMORY_METADATA_KEYS = (
    "total_params_b", "params_b", "block_count", "embedding_length",
    "attention_head_count", "head_count", "attention_head_count_kv",
    "head_count_kv", "attention_head_dimension", "head_dimension",
    "attention_key_length", "attention_value_length", "kv_cache_element_bytes",
    "vocab_size", "gpu_residency",
)


def memory_metadata(model: dict[str, Any]) -> dict[str, Any]:
    """Preserve architecture inputs when normalizing catalog records."""
    return {key: model[key] for key in MEMORY_METADATA_KEYS if key in model}


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


def _architecture_kv_bytes_per_token_f16(model: dict[str, Any]) -> float:
    """Return f16 K+V bytes per context token from architecture metadata.

    Zero means the catalog record does not carry enough metadata and callers
    must fall back to the parameter-scale heuristic.
    """
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
        return layer_kv_heads * (key_dimension + value_dimension) * 2.0
    return 0.0


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
    per_token_f16 = _architecture_kv_bytes_per_token_f16(model)
    if per_token_f16:
        element_bytes = _positive_number(model.get("kv_cache_element_bytes")) or 2.0
        kv_bytes = per_token_f16 * (element_bytes / 2.0) * context
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


def context_fitting_model(
    model: dict[str, Any], capacity_gb: float, *, tolerance_gb: float = 0.25,
) -> dict[str, Any]:
    """Reduce catalog context using architecture metadata, never a measured profile.

    Leave unqualified catalog entries unchanged. The ranker still checks fit
    afterward, including when even the minimum context cannot fit.
    """
    if model.get("_runtime_profile") or not _positive_number(model.get("block_count")):
        return model
    maximum = int(_positive_number(model.get("context_length")))
    if maximum <= 8192 or not _positive_number(capacity_gb):
        return model
    choices = {maximum, *(n for n in (8192, 16384, 32768, 65536, 131072, 262144) if n <= maximum)}
    for context in sorted(choices, reverse=True):
        if required_model_memory_gb(model, context_length=context) <= capacity_gb + tolerance_gb:
            if context == maximum:
                return model
            return {
                **model,
                "max_context_length": model.get("max_context_length") or maximum,
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

MIB = 1024.0 * 1024.0

# Bytes per KV element for llama.cpp cache types (block size 32 for quants).
KV_CACHE_TYPE_BYTES = {
    "f32": 4.0,
    "f16": 2.0,
    "bf16": 2.0,
    "q8_0": 34.0 / 32.0,
    "q5_1": 24.0 / 32.0,
    "q5_0": 22.0 / 32.0,
    "q4_1": 20.0 / 32.0,
    "q4_0": 18.0 / 32.0,
    "iq4_nl": 18.0 / 32.0,
}
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
# Allowance between this estimate and llama.cpp's own projection.
RESIDENCY_PLAN_GUARD_MIB = 64
STANDARD_CONTEXT_LENGTHS = (8192, 16384, 32768, 65536, 131072, 262144)
# Agent/Hermes floor. A fallback never trades context below it, and never
# shrinks a context that was already below it.
RESIDENCY_CONTEXT_FLOOR = 65536

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
    key = str(cache_type or "f16").strip().lower()
    return KV_CACHE_TYPE_BYTES.get(key, 2.0)


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

    With ``gpu_residency`` metadata (weights actually placed on the GPU, K+V
    bytes per token at f16, recurrent state, vocabulary) this reproduces the
    ``projected to use N MiB of device memory`` line of llama.cpp b9014; the
    Qwen3.5 9B and 27B entries match their fleet load logs to the MiB.
    Otherwise architecture metadata or the parameter-scale heuristic
    estimates the KV cache from the file size; ``gpu_residency_fit`` then
    also honours the catalog's declared VRAM class and runtime-profile
    estimate because those inputs are less exact.
    """
    try:
        context = int(context_length or model.get("context_length") or 0)
    except (TypeError, ValueError):
        context = 0
    context = max(context, 1024)
    kv_factor = (kv_cache_type_bytes(cache_type_k) + kv_cache_type_bytes(cache_type_v)) / 4.0
    residency = model.get("gpu_residency") if isinstance(model.get("gpu_residency"), dict) else {}
    parallel = max(int(parallel or 1), 1)
    gpu_count = max(int(gpu_count or 1), 1)

    residency_weights = _positive_number(residency.get("gpu_weights_mib"))
    residency_kv = _positive_number(residency.get("kv_bytes_per_token_f16"))
    if residency_weights and residency_kv:
        basis = str(residency.get("basis") or "measured")
        weights_mib = residency_weights
        kv_mib = residency_kv * context * kv_factor / MIB
        recurrent_mib = _positive_number(residency.get("recurrent_state_mib")) * parallel
        vocab = _positive_number(residency.get("vocab_size") or model.get("vocab_size"))
        embedding = _positive_number(
            residency.get("embedding_length") or model.get("embedding_length")
        )
    else:
        weights_mib = _positive_number(
            weight_size_mb if weight_size_mb is not None else model.get("size_mb")
        )
        per_token_f16 = _architecture_kv_bytes_per_token_f16(model)
        if per_token_f16:
            basis = "architecture"
            kv_mib = per_token_f16 * context * kv_factor / MIB
        else:
            basis = "declared"
            params_b = estimated_param_billions(model)
            kv_per_32k_gb = min(max(params_b * 0.12, 0.35), 3.5)
            kv_mib = kv_per_32k_gb * 1024.0 * (context / 32768.0) * kv_factor
        recurrent_mib = 0.0
        vocab = _positive_number(model.get("vocab_size"))
        embedding = _positive_number(model.get("embedding_length"))

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
        "cacheTypeK": str(cache_type_k or "f16").lower(),
        "cacheTypeV": str(cache_type_v or "f16").lower(),
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
) -> dict[str, Any] | None:
    """Return the smallest settings change expected to make full offload fit.

    ``required_mib`` is llama.cpp's full-offload projection for the current
    settings and ``available_mib`` the device memory it sees free before
    loading (read from the load log, or ``total - reserve - other`` when
    planning before launch). Steps, in order, stop at the first that fits:

    1. ubatch 256 and fit target 512 MiB (benchmarked: identical output);
    2. a q8_0 KV cache;
    3. a smaller standard context, never below the agent floor, never below
       a context that was already under it, and never for an explicitly
       requested context;
    4. a q4_0 KV cache.

    Returns ``None`` when no allowed configuration is expected to fit.
    """
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

    def result() -> dict[str, Any]:
        return {
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

    if fits():
        return result()

    if ubatch > RESIDENCY_FALLBACK_UBATCH:
        saved = compute * (1.0 - RESIDENCY_FALLBACK_UBATCH / ubatch)
        need -= saved
        compute -= saved
        ubatch = RESIDENCY_FALLBACK_UBATCH
        changes["LLAMA_ARG_UBATCH"] = str(ubatch)
        steps.append(f"ubatch {RESIDENCY_FALLBACK_UBATCH}")
    if fit_target > MIN_SAFE_FIT_TARGET_MIB:
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

    for next_type in kv_steps:
        quantize_kv(next_type)
        if fits():
            return result()
    return None


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
) -> dict[str, Any]:
    """Choose the configuration that keeps a model fully GPU-resident.

    Order: the configuration as declared; then ``plan_residency_fallback``
    (ubatch/fit target, quantized KV, context down to the agent floor) for
    models whose memory is known exactly (measured or GGUF-derived
    ``gpu_residency``) because the planner works with thin margins; then,
    for models with only architecture metadata, the largest standard context
    at the declared settings. Context is never traded below the agent floor,
    or below a configured context that is already under it. The result's
    ``overrides`` are the llama.cpp env values to launch with and
    ``contextLength`` the context to configure.
    """
    kwargs = {
        "total_vram_mb": total_vram_mb,
        "runtime_profile": runtime_profile,
        "env": env,
        "gpu_platform": gpu_platform,
        "gpu_count": gpu_count,
        "other_used_mib": other_used_mib,
        "weight_size_mb": weight_size_mb,
    }
    if context_length is None:
        if isinstance(runtime_profile, dict) and runtime_profile.get("context_length"):
            context_length = int(runtime_profile["context_length"])
        else:
            context_length = int(_positive_number(model.get("context_length"))) or None
    declared = gpu_residency_fit(model, context_length=context_length, **kwargs)
    if declared["fits"]:
        return {
            "fits": True, "residency": declared, "overrides": {}, "steps": [],
            "contextLength": declared["projection"]["contextLength"],
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
    )
    if plan is not None:
        overrides = {
            key: value for key, value in plan["changes"].items()
            if key.startswith("LLAMA_ARG_")
        }
        planned = gpu_residency_fit(
            model, context_length=plan["contextLength"], overrides=overrides, **kwargs,
        )
        if planned["fits"]:
            return {
                "fits": True, "residency": planned, "overrides": overrides,
                "steps": plan["steps"], "contextLength": plan["contextLength"],
            }

    if allow_context_reduction and not exact and _positive_number(model.get("block_count")):
        floor = min(RESIDENCY_CONTEXT_FLOOR, context)
        for candidate in sorted(STANDARD_CONTEXT_LENGTHS, reverse=True):
            if candidate >= context or candidate < floor:
                continue
            reduced = gpu_residency_fit(model, context_length=candidate, **kwargs)
            if reduced["fits"]:
                return {
                    "fits": True, "residency": reduced, "overrides": {},
                    "steps": [f"context {candidate}"], "contextLength": candidate,
                }
    return {
        "fits": False, "residency": declared, "overrides": {}, "steps": [],
        "contextLength": context,
    }


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
# The fit plan per device (common/fit.cpp). For MoE models llama.cpp can keep
# a layer "on the GPU" while moving its expert weights to system memory:
# "- CUDA0 (...): 49 layers (12 overflowing), ...". The load log still says
# every layer is offloaded, so the overflow count is the only signal.
_FIT_DEVICE_RE = re.compile(
    r"params_fit_impl:\s+- .+?: +\d+ layers \( *(?P<overflow>\d+) overflowing\)"
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
    "overflowing)",
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
    target = next(
        (m for m in (_TARGET_UNMET_RE.search(l) or _TARGET_MET_RE.search(l) for l in block) if m),
        None,
    )
    vocab = next((m for m in map(_VOCAB_RE.search, block) if m), None)
    embd = next((m for m in map(_EMBD_RE.search, block) if m), None)
    overflowing = sum(int(m.group("overflow")) for m in map(_FIT_DEVICE_RE.search, block) if m)
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
        "fitTargetMiB": int(target.group("target")) if target else None,
        "vocabSize": int(vocab.group("value")) if vocab else None,
        "embeddingLength": int(embd.group("value")) if embd else None,
        "overflowingLayers": overflowing,
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
        if projection and target:
            detail += (
                f"; llama.cpp projected {projection.group('need')} MiB against "
                f"{projection.group('free')} MiB free with a {target.group('target')} MiB margin"
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


def performance_core_count(
    *,
    sysfs_root: str = "/sys",
    cpuinfo_path: str = "/proc/cpuinfo",
    system: str | None = None,
) -> int:
    """Physical performance cores to use for CPU-side llama.cpp work.

    Used only when a runtime profile intentionally keeps work on the CPU (MoE
    expert offload). The compose default of 4 threads dates from DreamServer
    and ignores the host; every logical core is worse on hybrid CPUs: on the
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
    if "hybrid_cpu" in flags.split() or _INTEL_HYBRID_MODEL_RE.search(model_name):
        # A hybrid CPU whose P/E split is hidden. WSL exposes the Core Ultra 9
        # 285H as 16 identical cores with no hybrid flag or cpu_core set
        # (checked on windows-laptop-wsl-beta, 2026-09-25). Current Intel
        # mobile parts have 6 P-cores of 16 (Core Ultra 9 285H, 7 155H);
        # 3/8 of the cores approximates that without reaching the E-cores that
        # slowed decode in the benchmark.
        return max(physical * 3 // 8, 1)
    return physical
