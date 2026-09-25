#!/usr/bin/env python3
"""Select a pre-download ODS model from config/model-library.json.

This script is intentionally offline and deterministic. It only uses the
installer's detected hardware envelope plus the versioned model catalog; it
does not download GGUF metadata and it never treats catalog tok/s estimates as
measured performance.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions/services/dashboard-api"))
from model_memory import (
    context_fitting_model,
    detect_gpu_platform,
    estimated_context_kv_gb as estimated_context_kv_gb,
    estimated_param_billions as estimated_param_billions,
    is_discrete_gpu,
    memory_metadata,
    performance_core_count,
    required_model_memory_gb,
    resident_configuration,
    residency_is_decisive,
    runtime_memory_settings,
)


VRAM_FIT_TOLERANCE_GB = 0.25
POLICY = "context-aware-largest-capable-general-v1"
PIXEL_AGENT_POLICY = "pixel-agent-capability-v1"
SPARK_AARCH64_POLICY = "spark-aarch64-nv-ultra-a3b-v1"
SPARK_AARCH64_MODEL_ID = "qwen3.6-35b-a3b-ud-q4"
# Unified-memory hosts (Strix Halo SH_LARGE, future AMD/NV unified-memory
# tiers) hit the same coder-next correctness pathology as Spark aarch64.
# Until upstream fixes coder-next on unified-memory backends, route the
# qwen profile to the same 35B-A3B substitution used for Spark — same
# model id, separate policy tag so the recommendation_reason is honest
# about why the substitution fired.
UNIFIED_MEMORY_POLICY = "unified-memory-coder-next-a3b-v1"
UNIFIED_MEMORY_MODEL_ID = SPARK_AARCH64_MODEL_ID


def normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def normalize_profile(value: str | None) -> str:
    key = normalize_key(value or "qwen")
    if key in {"gemma", "gemma4", "gemma-4"}:
        return "gemma4"
    if key == "auto":
        return "auto"
    return "qwen"


def normalize_host_arch(value: str | None) -> str:
    key = normalize_key(value or "unknown")
    if key in {"aarch64", "arm64"}:
        return "arm64"
    if key in {"x86-64", "x86_64", "amd64", "x64"}:
        return "amd64"
    return key or "unknown"


def list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def value_enabled(value: Any) -> bool:
    return normalize_key(value) not in {"", "0", "false", "off", "no"}


def effective_profile(profile: str, backend: str, tier: str) -> str:
    if profile != "auto":
        return profile
    if normalize_key(tier) in {"cloud", "0", "t0"}:
        return "qwen"
    return "gemma4" if normalize_key(backend) in {"apple", "nvidia", "sycl"} else "qwen"


def normalize_model(raw: dict[str, Any]) -> dict[str, Any] | None:
    gguf_parts = raw.get("gguf_parts") if isinstance(raw.get("gguf_parts"), list) else []
    gguf = raw.get("gguf") or raw.get("gguf_file")
    if not gguf and gguf_parts and isinstance(gguf_parts[0], dict):
        gguf = gguf_parts[0].get("file")
    model_id = raw.get("id") or raw.get("llm_model_name") or raw.get("name") or gguf
    if not model_id or not gguf:
        return None
    try:
        size_mb = float(raw.get("size_mb") or 0)
    except (TypeError, ValueError):
        size_mb = 0.0
    try:
        vram_required = float(raw.get("vram_required_gb") or 0)
    except (TypeError, ValueError):
        vram_required = 0.0
    try:
        context_length = int(raw.get("context_length") or 0)
    except (TypeError, ValueError):
        context_length = 0
    app_compatibility = (
        raw.get("app_compatibility")
        if isinstance(raw.get("app_compatibility"), dict)
        else {}
    )
    agent_viability = (
        app_compatibility.get("agent_viability")
        if isinstance(app_compatibility.get("agent_viability"), dict)
        else {}
    )
    pixel_agent = (
        app_compatibility.get("pixel_agent")
        if isinstance(app_compatibility.get("pixel_agent"), dict)
        else {}
    )
    return {
        **memory_metadata(raw),
        "id": str(model_id),
        "name": raw.get("name") or str(model_id),
        "family": raw.get("family") or "",
        "llm_model_name": raw.get("llm_model_name") or str(model_id),
        "gguf_file": str(gguf),
        "gguf_url": raw.get("gguf_url") or "",
        "gguf_sha256": raw.get("gguf_sha256") or "",
        "gguf_parts": gguf_parts,
        "size_mb": size_mb,
        "vram_required_gb": vram_required,
        "context_length": context_length,
        "quantization": raw.get("quantization") or "",
        "specialty": raw.get("specialty") or "General",
        "llama_server_image": raw.get("llama_server_image") or "",
        "install_recommendation": value_enabled(raw.get("install_recommendation", True)),
        "agent_viability_status": normalize_key(agent_viability.get("status")),
        "pixel_agent_status": normalize_key(pixel_agent.get("status")),
        "runtime_profiles": raw.get("runtime_profiles") if isinstance(raw.get("runtime_profiles"), list) else [],
    }


def curated_source_allowed(model: dict[str, Any]) -> bool:
    """Return whether a catalog record is eligible for curated selection."""
    return str(model.get("source") or "").strip().lower() in {"", "curated"}


def load_catalog(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return [
        model for model in (
            normalize_model(raw)
            for raw in data.get("models", [])
            if curated_source_allowed(raw)
        )
        if model is not None
    ]


def usable_memory_gb(backend: str, memory_type: str, vram_mb: int, ram_gb: int) -> tuple[float, str]:
    backend_key = normalize_key(backend)
    memory_key = normalize_key(memory_type)
    if backend_key == "apple" or memory_key == "unified":
        # Unified-memory machines share RAM with the OS, Docker services, and
        # KV cache. Use only a bounded share for the model pick so 32GB-class
        # Macs/APUs are not handed a model that technically fits but thrashes.
        return max(float(ram_gb) * 0.55, 2.0), "unified system memory"
    if backend_key in {"cpu", "none", "unknown"} or vram_mb <= 0:
        return min(max(float(ram_gb) * 0.35, 3.0), 8.0), "system RAM"
    return float(vram_mb) / 1024.0, "GPU VRAM"


def fits(required_gb: float, capacity_gb: float) -> bool:
    return required_gb <= capacity_gb + VRAM_FIT_TOLERANCE_GB


def residency_candidate(model: dict[str, Any], runtime_profile: dict[str, Any] | None,
                        config: dict[str, Any]) -> dict[str, Any]:
    """Attach the residency decision (and any context change) to a candidate.

    A fitting configuration carries its overrides. When other processes hold
    the memory it needs (``idleFits`` but not ``fits``) the candidate carries
    the most memory-saving allowed configuration instead and is marked
    ``_residency_best_effort``: it is loaded anyway and its placement is
    reported. A candidate that cannot stay on this GPU even when it is idle
    keeps its declared settings (``_residency_spills``).
    """
    chosen = config
    best_effort = False
    if not config.get("fits") and config.get("idleFits") and config.get("bestEffort"):
        chosen = config["bestEffort"]
        best_effort = True
    candidate = {
        **model,
        "_gpu_residency": chosen["residency"],
        "_residency_fits": bool(config.get("fits")),
        "_residency_idle_fits": bool(config.get("idleFits")),
        "_residency_best_effort": best_effort,
        "_residency_spills": not bool(config.get("idleFits")),
        "_residency_overrides": (
            dict(chosen.get("overrides") or {}) if config.get("idleFits") else {}
        ),
        "_residency_steps": list(chosen.get("steps") or []) if config.get("idleFits") else [],
    }
    context = int(chosen.get("contextLength") or 0) if config.get("idleFits") else 0
    if context and context != effective_context_length(model, runtime_profile):
        candidate["max_context_length"] = model.get("max_context_length") or model.get("context_length")
        candidate["context_length"] = context
        candidate["_residency_context"] = context
    return candidate


def hardware_fit(model: dict[str, Any], capacity_gb: float, backend: str, memory_type: str,
                 vram_mb: int, *, gpu_platform: str | None = None,
                 gpu_count: int = 1, other_used_mib: float = 0.0) -> tuple[bool, dict[str, Any]]:
    """Return (fits, candidate): the capacity fit, then full GPU residency.

    The capacity fit (context-aware requirement against the GPU's memory) and
    therefore the ranking are unchanged. On a discrete GPU the candidate must
    also stay fully GPU-resident on the idle GPU: every layer, the KV cache
    and the compute buffers after the driver/runtime reserve and llama.cpp's
    free-memory margin, possibly with a smaller ubatch, a smaller margin or a
    quantized KV cache. The identity decision uses the idle GPU so a desktop
    or another process holding memory at install time never swaps the
    default model; its configuration is planned against ``other_used_mib``
    (see ``residency_candidate``). A candidate that fits only by spilling
    layers returns False with ``_capacity_fit`` set, so ``rank_models`` can
    fall back to it when nothing on this GPU is resident.
    """
    runtime_profile = (
        model.get("_runtime_profile")
        if isinstance(model.get("_runtime_profile"), dict)
        else None
    )
    candidate = context_fitting_model(model, capacity_gb)
    if not fits(effective_required_memory_gb(candidate, runtime_profile), capacity_gb):
        return False, candidate
    if not is_discrete_gpu(backend, memory_type, vram_mb):
        return True, candidate
    # Residency decides which model and context to pick only where the
    # estimate is exact: a catalog entry with measured or GGUF-derived
    # memory on a GPU inside the calibrated range (8 GB and up). Elsewhere (a
    # 4 or 6 GB card, or an entry with only architecture metadata) the
    # capacity fit alone picks, as before residency was enforced; the
    # estimate may still tune settings, and the placement is verified after
    # load and reported.
    decisive = residency_is_decisive(candidate, vram_mb, gpu_count)
    config = resident_configuration(
        candidate,
        total_vram_mb=vram_mb,
        runtime_profile=runtime_profile,
        context_length=effective_context_length(candidate, runtime_profile),
        gpu_platform=gpu_platform,
        gpu_count=gpu_count,
        other_used_mib=other_used_mib,
        allow_context_reduction=decisive,
    )
    resident = residency_candidate(candidate, runtime_profile, config)
    if decisive and not config["idleFits"]:
        return False, {**resident, "_capacity_fit": True}
    return True, resident


def selector_required_memory_gb(model: dict[str, Any]) -> float:
    return required_model_memory_gb(model)


def hardware_matching_profiles(model: dict[str, Any], backend: str, memory_type: str,
                               vram_mb: int, host_arch: str,
                               ram_gb: int | None = None) -> list[dict[str, Any]]:
    """Return profiles anchored to this hardware before system-RAM filtering.

    Once a catalog model has a profile for this exact backend/architecture/
    memory envelope, that profile is its safety contract. If the system-RAM
    requirement is not met, callers must not silently score the same model as
    though the hardware-specific profile did not exist.
    """
    backend_key = normalize_key(backend)
    memory_key = normalize_key(memory_type)
    arch_key = normalize_host_arch(host_arch)
    vram_gb = float(vram_mb or 0) / 1024.0
    matches: list[dict[str, Any]] = []
    for profile in model.get("runtime_profiles", []) or []:
        if not isinstance(profile, dict):
            continue
        if normalize_key(profile.get("backend")) not in {"", backend_key}:
            continue
        allowed_arches = {normalize_host_arch(item) for item in list_value(profile.get("host_arch"))}
        if allowed_arches and arch_key not in allowed_arches:
            continue
        required_memory_type = normalize_key(profile.get("memory_type"))
        if required_memory_type and required_memory_type != memory_key:
            continue
        try:
            # A RAM ceiling scopes the profile to a class of machines; it is
            # not an unmet prerequisite on machines above that class.
            if ram_gb is not None and profile.get("system_ram_max_gb") is not None and float(ram_gb) > float(profile["system_ram_max_gb"]):
                continue
            if profile.get("vram_min_gb") is not None and vram_gb < float(profile["vram_min_gb"]):
                continue
            if profile.get("vram_max_gb") is not None and vram_gb > float(profile["vram_max_gb"]):
                continue
        except (TypeError, ValueError):
            continue
        matches.append(profile)
    return matches


def matching_runtime_profile(model: dict[str, Any], backend: str, memory_type: str,
                             vram_mb: int, ram_gb: int, host_arch: str) -> dict[str, Any] | None:
    for profile in hardware_matching_profiles(
        model, backend, memory_type, vram_mb, host_arch, ram_gb
    ):
        try:
            if profile.get("system_ram_min_gb") is not None and float(ram_gb or 0) < float(profile["system_ram_min_gb"]):
                continue
            if profile.get("system_ram_max_gb") is not None and float(ram_gb or 0) > float(profile["system_ram_max_gb"]):
                continue
        except (TypeError, ValueError):
            continue
        return profile
    return None


def effective_context_length(model: dict[str, Any], runtime_profile: dict[str, Any] | None = None) -> int:
    if model.get("_residency_context"):
        return int(model["_residency_context"])
    if runtime_profile and runtime_profile.get("context_length"):
        return int(runtime_profile["context_length"])
    return int(model.get("context_length") or 0)


def effective_required_memory_gb(model: dict[str, Any],
                                 runtime_profile: dict[str, Any] | None = None,
                                 *, residency: bool = True) -> float:
    """GPU memory the candidate needs.

    With ``residency`` (the default, for display) a discrete-GPU candidate
    reports the total device memory it needs to stay fully resident, reserve
    and llama.cpp margin included. Ranking passes ``residency=False`` so the
    order of candidates stays the capacity ranking.
    """
    fit = model.get("_gpu_residency") if residency else None
    if isinstance(fit, dict) and fit.get("requiredGb") is not None:
        return float(fit["requiredGb"])
    if runtime_profile and runtime_profile.get("estimated_required_gb") is not None:
        return round(float(runtime_profile["estimated_required_gb"]), 2)
    if runtime_profile and runtime_profile.get("context_length"):
        model = {**model, "context_length": int(runtime_profile["context_length"])}
    return selector_required_memory_gb(model)


def family_allowed(model: dict[str, Any], profile: str) -> bool:
    family = normalize_key(model.get("family"))
    if profile == "gemma4":
        return family == "gemma4" or model.get("id") == "qwen3.5-2b-q4"
    return family != "gemma4"


def score_model(model: dict[str, Any], capacity_gb: float, profile: str) -> float:
    runtime_profile = model.get("_runtime_profile") if isinstance(model.get("_runtime_profile"), dict) else None
    required = effective_required_memory_gb(model, runtime_profile, residency=False)
    size_mb = max(float(model.get("size_mb") or 1), 1.0)
    context = max(effective_context_length(model, runtime_profile), 8192)
    specialty = str(model.get("specialty") or "General")
    family = normalize_key(model.get("family"))
    specialty_weight = {
        "Code": 4.4,
        "Quality": 4.1,
        "General": 3.8,
        "Balanced": 3.5,
        "Reasoning": 3.3,
        "Fast": 2.0,
        "Bootstrap": 1.0,
    }.get(specialty, 2.5)
    family_bonus = 0.35 if profile == "gemma4" and family == "gemma4" else 0.0
    family_bonus += 0.25 if profile in {"qwen", "auto"} and family == "qwen" else 0.0
    context_bonus = min(context / 32768, 4.0) * 0.18
    capability = min(size_mb / 1024, 48.0) * 0.24
    fit_ratio = required / max(capacity_gb, 1.0)
    headroom_penalty = 0.35 if fit_ratio > 0.98 else 0.15 if fit_ratio > 0.92 else 0.0
    return specialty_weight + family_bonus + context_bonus + capability - headroom_penalty


def install_recommendation_allowed(model: dict[str, Any]) -> bool:
    return bool(model.get("gguf_url")) and bool(model.get("install_recommendation", True))


def pixel_agent_ready(model: dict[str, Any]) -> bool:
    """Require an explicit real-Pixel capability verdict for the Pixel route."""
    return normalize_key(model.get("pixel_agent_status")) in {
        "verified",
        "pixel-agent-viable",
    }


def size_within_ceiling(model: dict[str, Any], max_size_mb: float) -> bool:
    """True if `model` respects an optional tier size ceiling.

    `max_size_mb` <= 0 means "no ceiling" (unbounded, current behavior).
    A small tolerance absorbs rounding differences between the tier map's
    declared LLM_MODEL_SIZE_MB and the catalog's size_mb for the same
    model, so the tier's own designated model is never excluded by its
    own ceiling.
    """
    if max_size_mb <= 0:
        return True
    size_mb = float(model.get("size_mb") or 0)
    tolerance_mb = max(max_size_mb * 0.02, 64.0)
    return size_mb <= max_size_mb + tolerance_mb


def rank_models(catalog: list[dict[str, Any]], capacity_gb: float, profile: str,
                installable_only: bool, backend: str, memory_type: str,
                vram_mb: int, ram_gb: int, host_arch: str,
                max_size_mb: float = 0,
                agent_ready_only: bool = False,
                gpu_platform: str | None = None,
                gpu_count: int = 1,
                other_used_mib: float = 0.0) -> list[dict[str, Any]]:
    def candidate_fit(model: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        return hardware_fit(
            model, capacity_gb, backend, memory_type, vram_mb,
            gpu_platform=gpu_platform, gpu_count=gpu_count,
            other_used_mib=other_used_mib,
        )

    def order(pool: list[tuple[float, dict[str, Any]]]) -> list[dict[str, Any]]:
        pool.sort(
            key=lambda item: (
                item[0],
                effective_required_memory_gb(
                    item[1], item[1].get("_runtime_profile"), residency=False
                ),
                effective_context_length(
                    item[1], item[1].get("_runtime_profile")
                ),
            ),
            reverse=True,
        )
        return [model for _, model in pool]

    spilling: list[dict[str, Any]] = []

    def ranked_candidates(*, enforce_size_ceiling: bool) -> list[dict[str, Any]]:
        ranked_pool: list[tuple[float, dict[str, Any]]] = []
        spill_pool: list[tuple[float, dict[str, Any]]] = []
        for model in catalog:
            if installable_only and not install_recommendation_allowed(model):
                continue
            if agent_ready_only and not pixel_agent_ready(model):
                continue
            if not family_allowed(model, profile):
                continue
            if enforce_size_ceiling and not size_within_ceiling(model, max_size_mb):
                continue
            runtime_profile = matching_runtime_profile(
                model, backend, memory_type, vram_mb, ram_gb, host_arch
            )
            if runtime_profile is None and hardware_matching_profiles(
                model, backend, memory_type, vram_mb, host_arch, ram_gb
            ):
                continue
            candidate_model = (
                {**model, "_runtime_profile": runtime_profile}
                if runtime_profile
                else model
            )
            candidate_fits, candidate_model = candidate_fit(candidate_model)
            if not candidate_fits:
                if candidate_model.get("_capacity_fit"):
                    spill_pool.append(
                        (score_model(candidate_model, capacity_gb, profile), candidate_model)
                    )
                continue
            ranked_pool.append(
                (score_model(candidate_model, capacity_gb, profile), candidate_model)
            )
        if not spilling:
            spilling.extend(order(spill_pool))
        return order(ranked_pool)

    ranked = ranked_candidates(enforce_size_ceiling=True)
    # A tier size ceiling is a resource preference, not permission to ship a
    # knowingly failed default model for Pixel. If no explicitly verified
    # agent model fits below the ceiling, relax only that ceiling while still
    # enforcing artifact pins, hardware memory fit, family, and installability.
    if not ranked and agent_ready_only and max_size_mb > 0:
        ranked = ranked_candidates(enforce_size_ceiling=False)
    if ranked:
        return ranked
    if spilling:
        # No capacity-ranked model stays fully on this GPU even when it is
        # idle (a 4GB card, where Phi-4 mini at 8K needs ~3.7 GB of device
        # memory against ~2.7 GB CUDA offers). Keep the capacity-ranked
        # choice with its declared settings, as before GPU residency was
        # enforced: its placement is verified after load and reported. Which
        # model these tiers should default to is the default-model work's
        # decision, not this estimator's (unmeasured on a 4GB card).
        return spilling
    if agent_ready_only:
        return []

    candidates = []
    for model in catalog:
        if installable_only and not install_recommendation_allowed(model):
            continue
        if not family_allowed(model, profile):
            continue
        if not size_within_ceiling(model, max_size_mb):
            continue
        runtime_profile = matching_runtime_profile(model, backend, memory_type, vram_mb, ram_gb, host_arch)
        if runtime_profile is None and hardware_matching_profiles(
            model, backend, memory_type, vram_mb, host_arch, ram_gb
        ):
            continue
        candidate_model = {**model, "_runtime_profile": runtime_profile} if runtime_profile else model
        candidate_fits, candidate_model = candidate_fit(candidate_model)
        if not candidate_fits:
            continue
        candidates.append((score_model(candidate_model, capacity_gb, profile), candidate_model))
    if not candidates:
        # A hardware-matching profile is a safety boundary. If it failed its
        # RAM gate, do not reintroduce that model through the generic fallback.
        fallback_pool = [
            model for model in catalog
            if (not installable_only or install_recommendation_allowed(model))
            and fits(selector_required_memory_gb(model), capacity_gb)
            and family_allowed(model, profile)
            and size_within_ceiling(model, max_size_mb)
            and not hardware_matching_profiles(
                model, backend, memory_type, vram_mb, host_arch, ram_gb
            )
        ] or [
            model for model in catalog
            if (not installable_only or install_recommendation_allowed(model)) and family_allowed(model, profile)
            and fits(selector_required_memory_gb(model), capacity_gb)
            and not hardware_matching_profiles(
                model, backend, memory_type, vram_mb, host_arch, ram_gb
            )
        ]
        if not fallback_pool:
            return []
        fallback = min(fallback_pool, key=lambda m: float(m.get("vram_required_gb") or 999))
        return [fallback]
    candidates.sort(
        key=lambda item: (
            item[0],
            effective_required_memory_gb(item[1], item[1].get("_runtime_profile"), residency=False),
            effective_context_length(item[1], item[1].get("_runtime_profile")),
        ),
        reverse=True,
    )
    return [model for _, model in candidates]


def arch_policy_model(catalog: list[dict[str, Any]], tier: str, profile: str,
                      host_arch: str, memory_type: str,
                      installable_only: bool,
                      selected_model: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """Return (model, policy_tag) for an architecture-specific override, or (None, None).

    Two routes both substitute coder-next → Qwen3.6-35B-A3B-UD on unified
    memory hosts where the qwen profile would otherwise pick coder-next
    (which produces all-`?` tokens on those backends — see in-source
    notes in installers/lib/tier-map.sh NV_ULTRA + SH_LARGE blocks):

      - nv-ultra + qwen + arm64: Spark / GB10 Grace Blackwell.
      - any-tier + qwen + memory_type=unified: Strix Halo + future
        unified-memory NV/AMD tiers. Memory-type is the authoritative
        signal (not arch or tier alone) because that's the actual
        characteristic that triggers the pathology.
    """
    if profile != "qwen":
        return None, None

    is_spark_aarch64 = (
        normalize_key(tier) == "nv-ultra"
        and normalize_host_arch(host_arch) == "arm64"
    )
    is_unified_coder_next = (
        normalize_key(memory_type) == "unified"
        and selected_model is not None
        and is_spark_aarch64_excluded_model(selected_model)
    )
    if not (is_spark_aarch64 or is_unified_coder_next):
        return None, None

    for model in catalog:
        if installable_only and not install_recommendation_allowed(model):
            continue
        if normalize_key(model.get("id")) == normalize_key(SPARK_AARCH64_MODEL_ID):
            policy = SPARK_AARCH64_POLICY if is_spark_aarch64 else UNIFIED_MEMORY_POLICY
            return model, policy
    return None, None


def is_spark_aarch64_excluded_model(model: dict[str, Any]) -> bool:
    """True if `model` is the coder-next entry that we route around on
    unified-memory backends. Function name preserved for backwards compat
    with existing callers; the broader semantic is "excluded on unified
    memory" (see arch_policy_model)."""
    return normalize_key(model.get("llm_model_name")) == "qwen3-coder-next"


def shell_value(value: Any) -> str:
    text = str(value or "")
    text = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'"{text}"'


def recommendation_reason(model: dict[str, Any], capacity_gb: float, memory_label: str,
                          backend: str, confidence: str) -> str:
    runtime_profile = model.get("_runtime_profile") if isinstance(model.get("_runtime_profile"), dict) else None
    context_k = int(effective_context_length(model, runtime_profile) / 1024)
    required = effective_required_memory_gb(model, runtime_profile)
    if isinstance(model.get("_gpu_residency"), dict) and not model.get("_residency_fits", True):
        residency = model["_gpu_residency"]
        projection = residency["projection"]
        if model.get("_residency_spills"):
            residency_note = (
                f" It does not stay fully on this GPU even when the GPU is idle: "
                f"llama.cpp needs about {projection['totalMiB'] / 1024:.1f}GB against "
                f"{max(residency['budgetMiB'], 0) / 1024:.1f}GB after the driver/runtime "
                f"reserve and its {residency['settings']['fitTargetMiB']} MiB margin, so "
                f"some layers will run on the CPU; placement is reported after load."
            )
        else:
            residency_note = (
                f" Other processes hold {residency['otherUsedMiB']:.0f} MiB of GPU "
                f"memory right now, so it is configured to use as little GPU memory "
                f"as it can at {context_k}K context"
                + (f" ({', '.join(model.get('_residency_steps') or [])})" if model.get("_residency_steps") else "")
                + "; if that memory stays in use some layers run on the CPU and the "
                "placement is reported after load."
            )
        return _base_reason(model, capacity_gb, memory_label, backend, runtime_profile) + residency_note
    if isinstance(model.get("_gpu_residency"), dict):
        residency = model["_gpu_residency"]
        label = (
            f" with {runtime_profile.get('label') or runtime_profile.get('id')}"
            if runtime_profile
            else ""
        )
        return (
            f"Catalog residency fit ({POLICY}): {model['name']}{label} stays fully "
            f"GPU-resident at {context_k}K context: llama.cpp needs about "
            f"{residency['projection']['totalMiB'] / 1024:.1f}GB on the GPU, "
            f"{required:g}GB of the {capacity_gb:.1f}GB {memory_label} once the "
            f"driver/runtime reserve and llama.cpp's "
            f"{residency['settings']['fitTargetMiB']} MiB free-memory margin are "
            f"counted, on {backend}. Throughput still requires a local benchmark "
            f"after first launch."
        )
    return _base_reason(model, capacity_gb, memory_label, backend, runtime_profile)


def _base_reason(model: dict[str, Any], capacity_gb: float, memory_label: str,
                 backend: str, runtime_profile: dict[str, Any] | None) -> str:
    context_k = int(effective_context_length(model, runtime_profile) / 1024)
    required = effective_required_memory_gb(model, runtime_profile, residency=False)
    if runtime_profile:
        label = runtime_profile.get("label") or runtime_profile.get("id") or "advanced runtime profile"
        runtime = runtime_profile.get("runtime") or "llama.cpp"
        return (
            f"Catalog runtime fit ({POLICY}): {model['name']} uses {label} "
            f"via {runtime}, needs about {required:g}GB GPU headroom plus "
            f"{runtime_profile.get('system_ram_min_gb', 'documented')}GB system RAM, "
            f"fits {capacity_gb:.1f}GB {memory_label} on {backend}, and gives "
            f"{context_k}K context. Throughput still requires a local benchmark after first launch."
        )
    return (
        f"Catalog fit ({POLICY}): {model['name']} needs "
        f"about {required:g}GB including context/KV, fits {capacity_gb:.1f}GB "
        f"{memory_label} on {backend}, and gives {context_k}K context. "
        f"Throughput requires a local benchmark after first launch."
    )


def arch_policy_reason(model: dict[str, Any], capacity_gb: float,
                       memory_label: str, policy_tag: str) -> str:
    context_k = int((model.get("context_length") or 0) / 1024)
    required = effective_required_memory_gb(model, model.get("_runtime_profile"))
    if policy_tag == UNIFIED_MEMORY_POLICY:
        rationale = (
            "is selected for unified-memory hosts (e.g. Strix Halo, future "
            "AMD/NV unified-memory tiers) because qwen3-coder-next produces "
            "all-`?` tokens on unified-memory backends"
        )
    else:
        rationale = (
            "is selected for arm64 NV_ULTRA Spark-class NVIDIA hosts because "
            "qwen3-coder-next is excluded on this architecture by the tier map"
        )
    return (
        f"Arch-aware catalog policy ({policy_tag}): {model['name']} "
        f"{rationale}. It needs about {required:g}GB including context/KV, "
        f"fits {capacity_gb:.1f}GB {memory_label}, and gives "
        f"{context_k}K context. Throughput requires a local benchmark after "
        f"first launch."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--backend", default="unknown")
    parser.add_argument("--memory-type", default="discrete")
    parser.add_argument("--vram-mb", type=int, default=0)
    parser.add_argument("--ram-gb", type=int, default=0)
    parser.add_argument("--profile", default="qwen")
    parser.add_argument("--tier", default="1")
    parser.add_argument(
        "--max-size-mb", type=float, default=0,
        help="Optional ceiling on selected model size_mb, e.g. the tier map's "
             "LLM_MODEL_SIZE_MB. 0 (default) leaves selection unbounded, "
             "picking the largest catalog model that fits available memory.",
    )
    parser.add_argument("--host-arch", default="unknown")
    parser.add_argument(
        "--platform", default="auto", choices=("auto", "linux", "wsl", "windows", "macos"),
        help="GPU driver platform for the device-memory reserve (auto-detected; "
             "WSL and Windows WDDM withhold more VRAM from CUDA than native Linux).",
    )
    parser.add_argument(
        "--gpu-count", type=int, default=1,
        help="Number of GPUs whose memory --vram-mb sums; each keeps its own reserve.",
    )
    parser.add_argument(
        "--other-used-mib", type=float, default=0.0,
        help="GPU memory (MiB, summed over GPUs) already held by other processes, "
             "such as a desktop drawn on the NVIDIA GPU. The model is chosen for the "
             "idle GPU; its settings are planned so it stays resident beside them.",
    )
    parser.add_argument("--installable-only", action="store_true")
    parser.add_argument(
        "--agent-ready-only",
        action="store_true",
        help="Select only models with an explicit verified Pixel capability verdict; "
             "used for the Pixel default route.",
    )
    parser.add_argument("--env", action="store_true", help="print shell assignments")
    args = parser.parse_args()

    catalog = load_catalog(args.catalog)
    if not catalog:
        print("error: model catalog is empty (no usable models)", file=sys.stderr)
        return 1
    profile = effective_profile(normalize_profile(args.profile), args.backend, args.tier)
    capacity_gb, memory_label = usable_memory_gb(args.backend, args.memory_type, args.vram_mb, args.ram_gb)
    gpu_platform = detect_gpu_platform() if args.platform == "auto" else args.platform
    gpu_count = max(int(args.gpu_count or 1), 1)
    other_used_mib = max(float(args.other_used_mib or 0.0), 0.0)
    confidence = "high" if args.backend not in {"unknown", "none"} and capacity_gb > 0 else "medium"
    ranked = rank_models(
        catalog,
        capacity_gb,
        profile,
        args.installable_only,
        args.backend,
        args.memory_type,
        args.vram_mb,
        args.ram_gb,
        args.host_arch,
        args.max_size_mb,
        args.agent_ready_only,
        gpu_platform=gpu_platform,
        gpu_count=gpu_count,
        other_used_mib=other_used_mib,
    )
    if not ranked:
        if args.agent_ready_only:
            message = "no explicitly verified Pixel agent model fits the detected hardware"
        else:
            message = "no installable model fits the detected hardware runtime profiles"
        print(f"error: {message}", file=sys.stderr)
        return 2
    arch_selected, arch_policy_tag = (None, None)
    if not args.agent_ready_only:
        arch_selected, arch_policy_tag = arch_policy_model(
            catalog, args.tier, profile, args.host_arch, args.memory_type,
            args.installable_only, ranked[0],
        )
    if arch_selected:
        arch_candidates = rank_models(
            [arch_selected], capacity_gb, profile, args.installable_only,
            args.backend, args.memory_type, args.vram_mb, args.ram_gb, args.host_arch,
            gpu_platform=gpu_platform, gpu_count=gpu_count, other_used_mib=other_used_mib,
        )
        arch_selected = arch_candidates[0] if arch_candidates else None
    if arch_selected:
        selected = arch_selected
        alternatives = [selected] + [
            model for model in ranked
            if model["id"] != selected["id"] and not is_spark_aarch64_excluded_model(model)
        ][:2]
        policy = f"{POLICY}+{arch_policy_tag}"
        source = "catalog_arch_policy_pre_download"
        reason = arch_policy_reason(selected, capacity_gb, memory_label, arch_policy_tag)
    else:
        selected = ranked[0]
        alternatives = ranked[:3]
        policy = f"{POLICY}+{PIXEL_AGENT_POLICY}" if args.agent_ready_only else POLICY
        source = "catalog_runtime_profile_pre_download" if selected.get("_runtime_profile") else "catalog_fit_pre_download"
        reason = recommendation_reason(selected, capacity_gb, memory_label, args.backend, confidence)
        if args.agent_ready_only:
            reason += " Pixel default selection requires an explicit verified Pixel capability verdict."
        if args.max_size_mb > 0 and size_within_ceiling(selected, args.max_size_mb):
            reason += (
                f" Bounded by --tier {args.tier}'s model size ceiling "
                f"({args.max_size_mb:g}MB); use ODS_DISABLE_CATALOG_MODEL_SELECTOR=true "
                f"to bypass."
            )
        elif args.max_size_mb > 0:
            reason += (
                f" Pixel capability readiness overrides --tier {args.tier}'s "
                f"{args.max_size_mb:g}MB model size preference because no verified "
                "agent model fits beneath it."
            )

    selected_public = {
        key: value for key, value in selected.items()
        if not str(key).startswith("_")
    }
    payload = {
        "policy": policy,
        "source": source,
        "confidence": confidence,
        "profile": profile,
        "host_arch": normalize_host_arch(args.host_arch),
        "memory_capacity_gb": round(capacity_gb, 1),
        "memory_label": memory_label,
        "gpu_residency": selected.get("_gpu_residency"),
        "gpu_residency_fits": selected.get("_residency_fits"),
        "gpu_residency_idle_fits": selected.get("_residency_idle_fits"),
        "gpu_residency_adjustments": selected.get("_residency_steps") or [],
        "selected": selected_public,
        "reason": reason,
        "alternatives": [
            {
                "id": model["id"],
                "name": model["name"],
                "gguf": model["gguf_file"],
                "vram_required_gb": model["vram_required_gb"],
                "estimated_required_gb": effective_required_memory_gb(model, model.get("_runtime_profile")),
                "context_length": effective_context_length(model, model.get("_runtime_profile")),
                "specialty": model["specialty"],
                "runtime_profile": (model.get("_runtime_profile") or {}).get("id"),
            }
            for model in alternatives
        ],
    }

    if not args.env:
        print(json.dumps(payload, indent=2))
        return 0

    alt_value = ";".join(
        f"{m['id']}:{effective_context_length(m, m.get('_runtime_profile'))}:{effective_required_memory_gb(m, m.get('_runtime_profile')):g}"
        for m in alternatives
    )
    runtime_profile = selected.get("_runtime_profile") if isinstance(selected.get("_runtime_profile"), dict) else None
    env = {
        "LLM_MODEL": selected["llm_model_name"],
        "GGUF_FILE": selected["gguf_file"],
        "GGUF_URL": selected["gguf_url"],
        "GGUF_SHA256": selected["gguf_sha256"],
        "MAX_CONTEXT": effective_context_length(selected, runtime_profile),
        "LLM_MODEL_SIZE_MB": int(round(float(selected["size_mb"]))),
        "MODEL_RECOMMENDATION_SOURCE": payload["source"],
        "MODEL_RECOMMENDATION_POLICY": payload["policy"],
        "MODEL_RECOMMENDATION_CONFIDENCE": payload["confidence"],
        "MODEL_RECOMMENDATION_REASON": payload["reason"],
        "MODEL_RECOMMENDED_ALTERNATIVES": alt_value,
        "PIXEL_AGENT_MODEL_READY": "true" if pixel_agent_ready(selected) else "false",
    }
    if runtime_profile:
        env["MODEL_RUNTIME_PROFILE"] = runtime_profile.get("id", "")
        env["MODEL_RUNTIME_PROFILE_LABEL"] = runtime_profile.get("label", "")
        env["MODEL_RUNTIME_PROFILE_SOURCE"] = runtime_profile.get("source_url", "")
        if runtime_profile.get("llama_server_image"):
            env["LLAMA_SERVER_IMAGE"] = runtime_profile["llama_server_image"]
        for key, value in (runtime_profile.get("env") or {}).items():
            if value is not None:
                env[str(key)] = value
        # Layers that intentionally stay on the CPU (MoE experts) run on the
        # performance cores only; compose's fixed 4 threads, or every logical
        # core, are measurably slower on hybrid CPUs.
        if runtime_memory_settings(runtime_profile)["intentionalOffload"] and "LLAMA_THREADS" not in env:
            threads = performance_core_count()
            if threads:
                env["LLAMA_THREADS"] = threads
    elif selected.get("llama_server_image"):
        env["LLAMA_SERVER_IMAGE"] = selected["llama_server_image"]
    # Settings the residency planner changed so every layer stays on the GPU
    # (for example a quantized KV cache on a card where f16 would spill).
    for key, value in (selected.get("_residency_overrides") or {}).items():
        env[str(key)] = value
    for key, value in env.items():
        print(f"{key}={shell_value(value)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
