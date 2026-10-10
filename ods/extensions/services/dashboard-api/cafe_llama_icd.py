"""ODS-shaped cafe-llama.cpp runtime adapter contract.

Only options verified against the currently supported runtime profile are
advertised. Experimental kernels, Turbo KV, MoE offload, SSD streaming and
speculative decoding must not become selectable until the exact binary and
environment mapping have been verified. Execution remains outside ICD.
"""
from __future__ import annotations

from typing import Any

RUNTIME_ID = "cafe-llama.cpp"

# Conservative allowlist. Expand only alongside binary-level validation and tests.
CAPABILITIES = {
    "kernel": ["baseline"],
    "kv_cache": ["f16", "q8_0"],
    "flash_attention": [True, False],
    "offload": ["none"],
    "speculation": ["none"],
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

    for dimension, allowed in CAPABILITIES.items():
        value = configuration.get(dimension)
        if value is not None and value not in allowed:
            raise ValueError(f"unsupported cafe configuration value for {dimension}: {value!r}")

    context = configuration.get("context")
    if context is not None and (
        isinstance(context, bool) or not isinstance(context, int) or context <= 0
    ):
        raise ValueError("context must be a positive integer")

    gpu_layers = configuration.get("gpu_layers")
    if gpu_layers is not None and (
        isinstance(gpu_layers, bool) or not isinstance(gpu_layers, int) or gpu_layers < 0
    ):
        raise ValueError("gpu_layers must be a non-negative integer")

    draft_tokens = configuration.get("draft_tokens") or 0
    if isinstance(draft_tokens, bool) or not isinstance(draft_tokens, int) or draft_tokens < 0:
        raise ValueError("draft_tokens must be a non-negative integer")
    if draft_tokens != 0:
        raise ValueError("draft tokens require a verified speculative-decoding profile")


def configuration_to_env(configuration: dict[str, Any]) -> dict[str, str]:
    validate_cafe_configuration(configuration)
    env: dict[str, str] = {}
    for dimension, key in ENV_MAPPING.items():
        value = configuration.get(dimension)
        if value is None:
            continue
        if dimension == "flash_attention":
            if not isinstance(value, bool):
                raise ValueError("flash_attention must be boolean")
            env[key] = "on" if value else "off"
        else:
            env[key] = str(value)
    env["CAFE_LLAMA_ENABLED"] = "true"
    return env
