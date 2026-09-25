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
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions/services/dashboard-api"))
from model_memory import (  # noqa: E402
    context_fitting_model as context_fitting_model,
    detect_gpu_platform,
    estimated_context_kv_gb as estimated_context_kv_gb,
    estimated_param_billions as estimated_param_billions,
    memory_metadata,
    performance_core_count,
    required_model_memory_gb,
    runtime_memory_settings,
)
from model_selection import (  # noqa: E402
    POLICY,
    Candidate,
    check_fit,
    family_allowed as family_allowed,
    hardware_matching_profiles as hardware_matching_profiles,
    install_recommendation_allowed as install_recommendation_allowed,
    list_value as list_value,
    matching_runtime_profile as matching_runtime_profile,
    memory_class,
    normalize_backend,
    normalize_host_arch as normalize_host_arch,
    normalize_key as normalize_key,
    rank_catalog_models,
    size_within_ceiling as size_within_ceiling,
    value_enabled as value_enabled,
)
from model_selection import pixel_agent_ready as _pixel_agent_ready  # noqa: E402
from model_selection import usable_memory_gb as _usable_memory_gb  # noqa: E402


VRAM_FIT_TOLERANCE_GB = 0.25
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
EXIT_NO_FIT = 2
EXIT_CHECK_FIT_FAILED = 3


def normalize_profile(value: str | None) -> str:
    key = normalize_key(value or "qwen")
    if key in {"gemma", "gemma4", "gemma-4"}:
        return "gemma4"
    if key == "auto":
        return "auto"
    return "qwen"


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
        "selection": raw.get("selection") if isinstance(raw.get("selection"), dict) else {},
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
    return _usable_memory_gb(backend, memory_type, vram_mb, ram_gb)


def fits(required_gb: float, capacity_gb: float) -> bool:
    """Legacy tolerance rule; the ranker applies model_memory.memory_fits."""
    return required_gb <= capacity_gb + VRAM_FIT_TOLERANCE_GB


def selector_required_memory_gb(model: dict[str, Any]) -> float:
    return required_model_memory_gb(model)


def effective_context_length(model: dict[str, Any], runtime_profile: dict[str, Any] | None = None) -> int:
    if runtime_profile and runtime_profile.get("context_length"):
        return int(runtime_profile["context_length"])
    return int(model.get("context_length") or 0)


def effective_required_memory_gb(model: dict[str, Any],
                                 runtime_profile: dict[str, Any] | None = None) -> float:
    selection = model.get("_selection")
    if isinstance(selection, dict) and selection.get("required_gb") is not None:
        return float(selection["required_gb"])
    return required_model_memory_gb(
        model,
        context_length=effective_context_length(model, runtime_profile),
        runtime_profile=runtime_profile,
    )


def pixel_agent_ready(model: dict[str, Any]) -> bool:
    """Require an explicit real-Pixel capability verdict for the Pixel route."""
    return _pixel_agent_ready(model)


def rank_candidates(catalog: list[dict[str, Any]], capacity_gb: float, profile: str,
                    installable_only: bool, backend: str, memory_type: str,
                    vram_mb: int, ram_gb: int, host_arch: str,
                    max_size_mb: float = 0,
                    agent_ready_only: bool = False,
                    min_context: int = 0,
                    require_min_context: bool = False,
                    include_size_tiebreak: bool = True,
                    *,
                    gpu_platform: str | None = None,
                    gpu_count: int = 1,
                    other_used_mib: float = 0.0) -> list[Candidate]:
    return rank_catalog_models(
        catalog,
        capacity_gb=capacity_gb,
        profile=profile,
        installable_only=installable_only,
        backend=backend,
        memory_type=memory_type,
        vram_mb=vram_mb,
        ram_gb=ram_gb,
        host_arch=host_arch,
        max_size_mb=max_size_mb,
        agent_ready_only=agent_ready_only,
        min_context=min_context,
        require_min_context=require_min_context,
        include_size_tiebreak=include_size_tiebreak,
        gpu_platform=gpu_platform,
        gpu_count=gpu_count,
        other_used_mib=other_used_mib,
    )


def rank_models(catalog: list[dict[str, Any]], capacity_gb: float, profile: str,
                installable_only: bool, backend: str, memory_type: str,
                vram_mb: int, ram_gb: int, host_arch: str,
                max_size_mb: float = 0,
                agent_ready_only: bool = False,
                min_context: int = 0,
                require_min_context: bool = False,
                *,
                gpu_platform: str | None = None,
                gpu_count: int = 1,
                other_used_mib: float = 0.0) -> list[dict[str, Any]]:
    """Ranked models at their planned context (see model_selection).

    On a discrete GPU each model carries its full-residency plan
    (``_gpu_residency``, ``_residency_overrides``, ``_residency_steps``,
    ``_residency_fits``, ``_residency_idle_fits``, ``_residency_best_effort``,
    ``_residency_spills``, ``_residency_decisive``).
    """
    return [
        candidate.as_model()
        for candidate in rank_candidates(
            catalog, capacity_gb, profile, installable_only, backend,
            memory_type, vram_mb, ram_gb, host_arch, max_size_mb,
            agent_ready_only, min_context, require_min_context,
            gpu_platform=gpu_platform, gpu_count=gpu_count,
            other_used_mib=other_used_mib,
        )
    ]


def arch_policy_model(catalog: list[dict[str, Any]], tier: str, profile: str,
                      host_arch: str, memory_type: str,
                      installable_only: bool,
                      selected_model: dict[str, Any] | None = None,
                      backend: str = "") -> tuple[dict[str, Any] | None, str | None]:
    """Return (model, policy_tag) for an architecture-specific override, or (None, None).

    Routes that substitute coder-next with Qwen3.6-35B-A3B-UD on unified-memory
    hosts (coder-next produces all-? tokens on those backends; see the
    in-source notes in installers/lib/tier-map.sh NV_ULTRA + SH_LARGE blocks):

      - nv-ultra + qwen + arm64: Spark / GB10 Grace Blackwell.
      - sh-large + amd + unified: every Strix Halo SH_LARGE host, whatever
        ranks first, matching installers/windows/lib/tier-map.ps1.
      - any-tier + qwen + memory_type=unified when coder-next ranks first:
        future unified-memory NV/AMD tiers. Memory-type is the authoritative
        signal (not arch or tier alone) because that's the actual
        characteristic that triggers the pathology.
    """
    if profile != "qwen":
        return None, None

    is_spark_aarch64 = (
        normalize_key(tier) == "nv-ultra"
        and normalize_host_arch(host_arch) == "arm64"
    )
    is_unified = normalize_key(memory_type) == "unified"
    is_strix_large = (
        is_unified
        and normalize_key(tier) == "sh-large"
        and normalize_backend(backend) == "amd"
    )
    is_unified_coder_next = (
        is_unified
        and selected_model is not None
        and is_spark_aarch64_excluded_model(selected_model)
    )
    if not (is_spark_aarch64 or is_strix_large or is_unified_coder_next):
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


def _requirement_breakdown(model: dict[str, Any]) -> str:
    selection = model.get("_selection") if isinstance(model.get("_selection"), dict) else {}
    source = selection.get("estimate_source")
    estimate = selection.get("estimate") if isinstance(selection.get("estimate"), dict) else {}
    if source == "architecture" and estimate:
        parts = [
            f"weights {estimate.get('weights_gib', 0):.2f}",
            f"KV {estimate.get('kv_gib', 0):.2f}",
        ]
        if estimate.get("recurrent_state_gib"):
            parts.append(f"recurrent state {estimate['recurrent_state_gib']:.2f}")
        parts.append(f"overhead {estimate.get('compute_overhead_gib', 0):.2f}")
        if selection.get("memory_class") == "cpu" and estimate.get("host_checkpoint_gib"):
            parts.append(f"context checkpoints {estimate['host_checkpoint_gib']:.2f}")
        return " + ".join(parts)
    if source == "runtime-profile":
        return "measured runtime-profile budget"
    return "catalog estimate including context/KV"


def _margin_text(model: dict[str, Any]) -> str:
    selection = model.get("_selection") if isinstance(model.get("_selection"), dict) else {}
    margin = float(selection.get("fit_margin_gb") or 0)
    if margin < 0:
        return f"within the {abs(margin):g} GiB catalog tolerance"
    if margin > 0:
        return f"leaving at least {margin:g} GiB free"
    return "within budget"


def residency_note(model: dict[str, Any]) -> str:
    """What full GPU residency adds to a discrete-GPU recommendation."""
    residency = model.get("_gpu_residency")
    if not isinstance(residency, dict):
        return ""
    projection = residency.get("projection") or {}
    runtime_profile = model.get("_runtime_profile") if isinstance(model.get("_runtime_profile"), dict) else None
    context_k = int(effective_context_length(model, runtime_profile) / 1024)
    steps = list(model.get("_residency_steps") or [])
    if model.get("_residency_spills"):
        return (
            f" It does not stay fully on this GPU even when the GPU is idle: "
            f"llama.cpp needs about {float(projection.get('totalMiB') or 0) / 1024:.1f}GB against "
            f"{max(float(residency.get('budgetMiB') or 0), 0) / 1024:.1f}GB after the driver/runtime "
            f"reserve and its {residency.get('fitTargetMiB')} MiB margin, so some layers "
            f"will run on the CPU; placement is reported after load."
        )
    if model.get("_residency_best_effort"):
        return (
            f" Other processes hold {float(residency.get('otherUsedMiB') or 0):.0f} MiB of GPU "
            f"memory right now, so it is configured to use as little GPU memory as it "
            f"can at {context_k}K context"
            + (f" ({', '.join(steps)})" if steps else "")
            + "; if that memory stays in use some layers run on the CPU and the "
            "placement is reported after load."
        )
    note = (
        f" Stays fully on the GPU: llama.cpp needs about "
        f"{float(projection.get('totalMiB') or 0) / 1024:.1f}GB of the "
        f"{max(float(residency.get('availableMiB') or 0), 0) / 1024:.1f}GB it can use after the "
        f"{residency.get('reserveMiB')} MiB driver/runtime reserve, keeping its "
        f"{residency.get('fitTargetMiB')} MiB margin free"
    )
    return note + (f" (with {', '.join(steps)})." if steps else ".")


def recommendation_reason(model: dict[str, Any], capacity_gb: float, memory_label: str,
                          backend: str, confidence: str) -> str:
    return _curated_reason(model, capacity_gb, memory_label, backend, confidence) + residency_note(model)


def _curated_reason(model: dict[str, Any], capacity_gb: float, memory_label: str,
                    backend: str, confidence: str) -> str:
    runtime_profile = model.get("_runtime_profile") if isinstance(model.get("_runtime_profile"), dict) else None
    context_k = int(effective_context_length(model, runtime_profile) / 1024)
    required = effective_required_memory_gb(model, runtime_profile)
    selection = model.get("_selection") if isinstance(model.get("_selection"), dict) else {}
    mclass = selection.get("memory_class") or memory_class(backend, "", 0)
    if runtime_profile:
        label = runtime_profile.get("label") or runtime_profile.get("id") or "advanced runtime profile"
        runtime = runtime_profile.get("runtime") or "llama.cpp"
        ram_note = (
            f" plus {runtime_profile['system_ram_min_gb']}GB system RAM"
            if runtime_profile.get("system_ram_min_gb") is not None
            else ""
        )
        return (
            f"Curated runtime fit ({POLICY}): {model['name']} is the highest-priority "
            f"installable model for {mclass} memory; it uses {label} via {runtime}, "
            f"needs about {required:g} GiB ({_requirement_breakdown(model)}){ram_note}, "
            f"fits {capacity_gb:.1f} GiB {memory_label} on {backend} "
            f"({_margin_text(model)}), and gives {context_k}K context. "
            f"Throughput still requires a local benchmark after first launch."
        )
    return (
        f"Curated fit ({POLICY}): {model['name']} is the highest-priority installable "
        f"model for {mclass} memory that fits {capacity_gb:.1f} GiB {memory_label} on "
        f"{backend} ({_margin_text(model)}) at {context_k}K context; it needs about "
        f"{required:g} GiB ({_requirement_breakdown(model)}). "
        f"Throughput requires a local benchmark after first launch."
    )


def arch_policy_reason(model: dict[str, Any], capacity_gb: float,
                       memory_label: str, policy_tag: str) -> str:
    runtime_profile = model.get("_runtime_profile") if isinstance(model.get("_runtime_profile"), dict) else None
    context_k = int(effective_context_length(model, runtime_profile) / 1024)
    required = effective_required_memory_gb(model, runtime_profile)
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
        f"{rationale}. It needs about {required:g} GiB "
        f"({_requirement_breakdown(model)}), fits {capacity_gb:.1f} GiB "
        f"{memory_label}, and gives {context_k}K context. Throughput requires "
        f"a local benchmark after first launch."
    )


def _alternative_payload(model: dict[str, Any]) -> dict[str, Any]:
    runtime_profile = model.get("_runtime_profile") if isinstance(model.get("_runtime_profile"), dict) else None
    selection = model.get("_selection") if isinstance(model.get("_selection"), dict) else {}
    return {
        "id": model["id"],
        "name": model["name"],
        "gguf": model["gguf_file"],
        "vram_required_gb": model["vram_required_gb"],
        "estimated_required_gb": effective_required_memory_gb(model, runtime_profile),
        "context_length": effective_context_length(model, runtime_profile),
        "specialty": model["specialty"],
        "runtime_profile": (runtime_profile or {}).get("id"),
        "meets_min_context": selection.get("meets_min_context"),
        "estimate_source": selection.get("estimate_source"),
        "estimate": selection.get("estimate"),
    }


def _check_fit_main(args: argparse.Namespace, catalog: list[dict[str, Any]],
                    capacity_gb: float, *, gpu_platform: str, gpu_count: int,
                    other_used_mib: float) -> int:
    model_key = normalize_key(args.model_id)
    model = next(
        (
            item for item in catalog
            if model_key in {
                normalize_key(item.get("id")),
                normalize_key(item.get("llm_model_name")),
                normalize_key(item.get("gguf_file")),
            }
        ),
        None,
    )
    if model is None:
        print(f"error: model {args.model_id!r} is not in the catalog", file=sys.stderr)
        return 1
    runtime_profile = None
    if args.runtime_profile:
        runtime_profile = next(
            (
                profile for profile in model.get("runtime_profiles") or []
                if isinstance(profile, dict) and profile.get("id") == args.runtime_profile
            ),
            None,
        )
    context = int(args.context or model.get("context_length") or 0)
    result = check_fit(
        model,
        context_length=context,
        capacity_gb=capacity_gb,
        mclass=memory_class(args.backend, args.memory_type, args.vram_mb),
        runtime_profile=runtime_profile,
        vram_mb=args.vram_mb,
        gpu_platform=gpu_platform,
        gpu_count=gpu_count,
        other_used_mib=other_used_mib,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["fits"] else EXIT_CHECK_FIT_FAILED


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
             "LLM_MODEL_SIZE_MB. 0 (default) leaves selection unbounded.",
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
    parser.add_argument(
        "--min-context", type=int, default=0,
        help="Soft context floor (the installers pass Hermes's 65536): a model "
             "that fits at or above it outranks every model that does not.",
    )
    parser.add_argument(
        "--require-min-context", action="store_true",
        help="Make --min-context a hard floor: exit 2 when nothing fits at it.",
    )
    parser.add_argument(
        "--check-fit", action="store_true",
        help="Check one model instead of ranking: prints JSON and exits 0 when "
             "--model-id fits at --context, 3 when it does not.",
    )
    parser.add_argument("--model-id", default="")
    parser.add_argument("--context", type=int, default=0)
    parser.add_argument("--runtime-profile", default="")
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
    if args.check_fit:
        if not args.model_id:
            print("error: --check-fit needs --model-id", file=sys.stderr)
            return 1
        return _check_fit_main(
            args, catalog, capacity_gb,
            gpu_platform=gpu_platform, gpu_count=gpu_count, other_used_mib=other_used_mib,
        )

    mclass = memory_class(args.backend, args.memory_type, args.vram_mb)
    confidence = "high" if args.backend not in {"unknown", "none"} and capacity_gb > 0 else "medium"
    min_context = max(int(args.min_context or 0), 0)
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
        min_context,
        args.require_min_context,
        gpu_platform=gpu_platform,
        gpu_count=gpu_count,
        other_used_mib=other_used_mib,
    )
    if not ranked:
        if args.agent_ready_only:
            message = "no explicitly verified Pixel agent model fits the detected hardware"
        elif args.require_min_context and min_context:
            message = f"no installable model fits the detected hardware at {min_context} context"
        else:
            message = "no installable model fits the detected hardware runtime profiles"
        print(f"error: {message}", file=sys.stderr)
        return EXIT_NO_FIT
    arch_selected, arch_policy_tag = (None, None)
    if not args.agent_ready_only:
        arch_selected, arch_policy_tag = arch_policy_model(
            catalog, args.tier, profile, args.host_arch, args.memory_type,
            args.installable_only, ranked[0], backend=args.backend,
        )
    if arch_selected:
        arch_candidates = rank_models(
            [arch_selected], capacity_gb, profile, args.installable_only,
            args.backend, args.memory_type, args.vram_mb, args.ram_gb, args.host_arch,
            0, False, min_context, args.require_min_context,
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
        reason = arch_policy_reason(selected, capacity_gb, memory_label, arch_policy_tag) + residency_note(selected)
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
        elif args.max_size_mb > 0 and args.agent_ready_only:
            reason += (
                f" Pixel capability readiness overrides --tier {args.tier}'s "
                f"{args.max_size_mb:g}MB model size preference because no verified "
                "agent model fits beneath it."
            )
        elif args.max_size_mb > 0:
            reason += (
                f" No installable model fits beneath --tier {args.tier}'s "
                f"{args.max_size_mb:g}MB model size preference, so it was relaxed."
            )
    selected_selection = selected.get("_selection") if isinstance(selected.get("_selection"), dict) else {}
    if min_context and not selected_selection.get("meets_min_context", True):
        reason += (
            f" No installable model fits this hardware at the {min_context} context floor; "
            f"this is the largest context that fits."
        )

    selected_public = {key: value for key, value in selected.items() if not key.startswith("_")}
    payload = {
        "policy": policy,
        "source": source,
        "confidence": confidence,
        "profile": profile,
        "host_arch": normalize_host_arch(args.host_arch),
        "memory_capacity_gb": round(capacity_gb, 1),
        "memory_label": memory_label,
        "memory_class": mclass,
        "fit_margin_gb": selected_selection.get("fit_margin_gb"),
        "min_context": min_context,
        "meets_min_context": selected_selection.get("meets_min_context", True),
        "gpu_platform": gpu_platform if mclass == "discrete" else None,
        "gpu_residency": selected.get("_gpu_residency"),
        "gpu_residency_decisive": selected.get("_residency_decisive"),
        "gpu_residency_fits": selected.get("_residency_fits"),
        "gpu_residency_idle_fits": selected.get("_residency_idle_fits"),
        "gpu_residency_adjustments": selected.get("_residency_steps") or [],
        "gpu_residency_overrides": selected.get("_residency_overrides") or {},
        "selected": selected_public,
        "reason": reason,
        "alternatives": [_alternative_payload(model) for model in alternatives],
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
    # Settings the residency plan chose so every layer stays on the GPU (for
    # example ubatch 256 and a 512 MiB fit target on a 4 GB card).
    for key, value in (selected.get("_residency_overrides") or {}).items():
        env[str(key)] = value
    for key, value in env.items():
        print(f"{key}={shell_value(value)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
