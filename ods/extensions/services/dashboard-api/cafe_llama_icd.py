"""ODS-shaped cafe-llama.cpp runtime adapter contract.

This module is intentionally small: core ICD stays runtime-agnostic while
cafe-llama exposes its known configuration vocabulary and environment mapping.
Execution remains outside runtime_selection.
"""
from __future__ import annotations

from typing import Any

RUNTIME_ID = "cafe-llama.cpp"

CAPABILITIES = {
    "kernel": ["baseline", "ptq1-mmV"],
    "kv_cache": ["f16", "q8_0", "turbo2", "turbo3", "turbo4"],
    "flash_attention": [True, False],
    "offload": ["none", "host-moe", "cpu-moe", "ssd"],
    "speculation": ["none", "draft-mtp"],
}

ENV_MAPPING = {
    "gpu_layers": "LLAMA_ARG_N_GPU_LAYERS",
    "context": "MAX_CONTEXT",
    "kv_cache": "LLAMA_ARG_CACHE_TYPE_K",
    "flash_attention": "LLAMA_ARG_FLASH_ATTN",
    "speculation": "LLAMA_ARG_SPEC_TYPE",
    "draft_tokens": "LLAMA_ARG_SPEC_DRAFT_N_MAX",
}


def validate_cafe_configuration(configuration: dict[str, Any]) -> None:
    if configuration.get("runtime") != RUNTIME_ID:
        raise ValueError("configuration is not a cafe-llama.cpp configuration")

    kv = str(configuration.get("kv_cache") or "").lower()
    if kv.startswith("turbo") and configuration.get("flash_attention") is not True:
        raise ValueError("turbo KV requires flash attention")

    speculation = str(configuration.get("speculation") or "").lower()
    draft_tokens = int(configuration.get("draft_tokens") or 0)
    if speculation in {"none", "off"} and draft_tokens != 0:
        raise ValueError("non-speculative configuration cannot have draft tokens")
    if speculation == "draft-mtp" and draft_tokens <= 0:
        raise ValueError("draft-mtp requires draft tokens")


def configuration_to_env(configuration: dict[str, Any]) -> dict[str, str]:
    validate_cafe_configuration(configuration)
    env: dict[str, str] = {}
    for dimension, key in ENV_MAPPING.items():
        value = configuration.get(dimension)
        if value is None:
            continue
        if dimension == "flash_attention":
            env[key] = "on" if value else "off"
        else:
            env[key] = str(value)
    offload = str(configuration.get("offload") or "none").lower()
    env["CAFE_LLAMA_ENABLED"] = "true"
    if offload == "host-moe":
        env["LLAMA_ARG_HOST_MOE"] = "on"
    elif offload == "cpu-moe":
        env["LLAMA_ARG_CPU_MOE"] = "on"
    elif offload == "ssd":
        env["LLAMA_ARG_SSD_STREAMING"] = "on"
    return env
