#!/usr/bin/env python3
"""Select a pre-download DreamServer model from config/model-library.json.

This script is intentionally offline and deterministic. It only uses the
installer's detected hardware envelope plus the versioned model catalog; it
does not download GGUF metadata and it never treats catalog tok/s estimates as
measured performance.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any


VRAM_FIT_TOLERANCE_GB = 0.25
POLICY = "context-aware-largest-capable-general-v1"
SPARK_AARCH64_POLICY = "spark-aarch64-nv-ultra-a3b-v1"
SPARK_AARCH64_MODEL_ID = "qwen3.6-35b-a3b-ud-q4"


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
    return {
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
        "runtime_profiles": raw.get("runtime_profiles") if isinstance(raw.get("runtime_profiles"), list) else [],
    }


def load_catalog(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return [
        model for model in (normalize_model(raw) for raw in data.get("models", []))
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


def estimated_param_billions(model: dict[str, Any]) -> float:
    for key in ("total_params_b", "params_b"):
        try:
            value = float(model.get(key) or 0)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    numbers: list[float] = []
    for text in (model.get("id"), model.get("name"), model.get("llm_model_name"), model.get("gguf_file")):
        numbers.extend(float(match) for match in re.findall(r"(\d+(?:\.\d+)?)\s*b", str(text or ""), re.I))
    if numbers:
        return max(numbers)
    size_mb = float(model.get("size_mb") or 0)
    if size_mb > 0:
        return max(size_mb / 600.0, 1.0)
    return 4.0


def estimated_context_kv_gb(model: dict[str, Any]) -> float:
    context = max(int(model.get("context_length") or 0), 8192)
    params_b = estimated_param_billions(model)
    kv_per_32k_gb = min(max(params_b * 0.12, 0.35), 3.5)
    return round(kv_per_32k_gb * (context / 32768.0), 2)


def selector_required_memory_gb(model: dict[str, Any]) -> float:
    declared = float(model.get("vram_required_gb") or 0)
    size_gb = float(model.get("size_mb") or 0) / 1024.0
    if size_gb <= 0:
        return round(declared, 2)
    return round(max(declared, size_gb + estimated_context_kv_gb(model)), 2)


def matching_runtime_profile(model: dict[str, Any], backend: str, memory_type: str,
                             vram_mb: int, ram_gb: int, host_arch: str) -> dict[str, Any] | None:
    backend_key = normalize_key(backend)
    memory_key = normalize_key(memory_type)
    arch_key = normalize_host_arch(host_arch)
    vram_gb = float(vram_mb or 0) / 1024.0
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
            if profile.get("vram_min_gb") is not None and vram_gb < float(profile["vram_min_gb"]):
                continue
            if profile.get("vram_max_gb") is not None and vram_gb > float(profile["vram_max_gb"]):
                continue
            if profile.get("system_ram_min_gb") is not None and float(ram_gb or 0) < float(profile["system_ram_min_gb"]):
                continue
        except (TypeError, ValueError):
            continue
        return profile
    return None


def effective_context_length(model: dict[str, Any], runtime_profile: dict[str, Any] | None = None) -> int:
    if runtime_profile and runtime_profile.get("context_length"):
        return int(runtime_profile["context_length"])
    return int(model.get("context_length") or 0)


def effective_required_memory_gb(model: dict[str, Any],
                                 runtime_profile: dict[str, Any] | None = None) -> float:
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
    required = effective_required_memory_gb(model, runtime_profile)
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


def rank_models(catalog: list[dict[str, Any]], capacity_gb: float, profile: str,
                installable_only: bool, backend: str, memory_type: str,
                vram_mb: int, ram_gb: int, host_arch: str) -> list[dict[str, Any]]:
    candidates = []
    for model in catalog:
        if installable_only and not model.get("gguf_url"):
            continue
        if not family_allowed(model, profile):
            continue
        runtime_profile = matching_runtime_profile(model, backend, memory_type, vram_mb, ram_gb, host_arch)
        candidate_model = {**model, "_runtime_profile": runtime_profile} if runtime_profile else model
        required = effective_required_memory_gb(candidate_model, runtime_profile)
        if not fits(required, capacity_gb):
            continue
        candidates.append((score_model(candidate_model, capacity_gb, profile), candidate_model))
    if not candidates:
        fallback_pool = [
            model for model in catalog
            if (not installable_only or model.get("gguf_url")) and family_allowed(model, profile)
        ] or catalog
        fallback = min(fallback_pool, key=lambda m: float(m.get("vram_required_gb") or 999))
        return [fallback]
    candidates.sort(
        key=lambda item: (
            item[0],
            effective_required_memory_gb(item[1], item[1].get("_runtime_profile")),
            effective_context_length(item[1], item[1].get("_runtime_profile")),
        ),
        reverse=True,
    )
    return [model for _, model in candidates]


def arch_policy_model(catalog: list[dict[str, Any]], tier: str, profile: str,
                      host_arch: str, installable_only: bool) -> dict[str, Any] | None:
    """Return an architecture-specific model override when the tier map requires one."""
    if normalize_key(tier) != "nv-ultra" or profile != "qwen" or normalize_host_arch(host_arch) != "arm64":
        return None
    for model in catalog:
        if installable_only and not model.get("gguf_url"):
            continue
        if normalize_key(model.get("id")) == normalize_key(SPARK_AARCH64_MODEL_ID):
            return model
    return None


def is_spark_aarch64_excluded_model(model: dict[str, Any]) -> bool:
    return normalize_key(model.get("llm_model_name")) == "qwen3-coder-next"


def shell_value(value: Any) -> str:
    return shlex.quote(str(value or ""))


def recommendation_reason(model: dict[str, Any], capacity_gb: float, memory_label: str,
                          backend: str, confidence: str) -> str:
    runtime_profile = model.get("_runtime_profile") if isinstance(model.get("_runtime_profile"), dict) else None
    context_k = int(effective_context_length(model, runtime_profile) / 1024)
    required = effective_required_memory_gb(model, runtime_profile)
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


def arch_policy_reason(model: dict[str, Any], capacity_gb: float, memory_label: str) -> str:
    context_k = int((model.get("context_length") or 0) / 1024)
    required = selector_required_memory_gb(model)
    return (
        f"Arch-aware catalog policy ({SPARK_AARCH64_POLICY}): {model['name']} "
        f"is selected for arm64 NV_ULTRA Spark-class NVIDIA hosts because "
        f"qwen3-coder-next is excluded on this architecture by the tier map. "
        f"It needs about {required:g}GB including context/KV, fits {capacity_gb:.1f}GB "
        f"{memory_label}, and gives {context_k}K context. "
        f"Throughput requires a local benchmark after first launch."
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
    parser.add_argument("--host-arch", default="unknown")
    parser.add_argument("--installable-only", action="store_true")
    parser.add_argument("--env", action="store_true", help="print shell assignments")
    args = parser.parse_args()

    catalog = load_catalog(args.catalog)
    profile = effective_profile(normalize_profile(args.profile), args.backend, args.tier)
    capacity_gb, memory_label = usable_memory_gb(args.backend, args.memory_type, args.vram_mb, args.ram_gb)
    confidence = "high" if args.backend not in {"unknown", "none"} and capacity_gb > 0 else "medium"
    arch_selected = arch_policy_model(catalog, args.tier, profile, args.host_arch, args.installable_only)
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
    )
    if arch_selected:
        selected = arch_selected
        alternatives = [selected] + [
            model for model in ranked
            if model["id"] != selected["id"] and not is_spark_aarch64_excluded_model(model)
        ][:2]
        policy = f"{POLICY}+{SPARK_AARCH64_POLICY}"
        source = "catalog_arch_policy_pre_download"
        reason = arch_policy_reason(selected, capacity_gb, memory_label)
    else:
        selected = ranked[0]
        alternatives = ranked[:3]
        policy = POLICY
        source = "catalog_runtime_profile_pre_download" if selected.get("_runtime_profile") else "catalog_fit_pre_download"
        reason = recommendation_reason(selected, capacity_gb, memory_label, args.backend, confidence)

    selected_public = {key: value for key, value in selected.items() if key != "_runtime_profile"}
    payload = {
        "policy": policy,
        "source": source,
        "confidence": confidence,
        "profile": profile,
        "host_arch": normalize_host_arch(args.host_arch),
        "memory_capacity_gb": round(capacity_gb, 1),
        "memory_label": memory_label,
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
    elif selected.get("llama_server_image"):
        env["LLAMA_SERVER_IMAGE"] = selected["llama_server_image"]
    for key, value in env.items():
        print(f"{key}={shell_value(value)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
