"""Cafe-llama.cpp profile for ODS Inference Configuration Discovery.

The catalog is intentionally broad: it records upstream-documented features and
build-dependent features. A catalog entry is not proof that the selected binary,
backend, model, or ODS activation path supports it. Pass runtime_capabilities
from the probed binary to enforce the exact execution profile.
"""
from __future__ import annotations

from typing import Any

from cafe_llama_parameters import (
    CAPABILITY_VOCABULARY,
    DISCOVERY_CAPABILITIES,
    PARAMETERS,
    parameters_by_readiness,
)

RUNTIME_ID = "cafe-llama.cpp"

# Public feature vocabulary: broad by design, including documented and
# build-dependent Cafe capabilities. The full parameter details live alongside.
CAPABILITIES = CAPABILITY_VOCABULARY

_DIMENSION_VOCABULARY = {
    "kernel": CAPABILITIES["kernel_tuning"],
    "kv_cache": CAPABILITIES["kv_cache"],
    "offload": ["none", *CAPABILITIES["moe_placement"]],
    "speculation": CAPABILITIES["speculation"],
    "flash_attention": [True, False],
}


def validate_cafe_configuration(
    configuration: dict[str, Any],
    runtime_capabilities: dict[str, list[Any]] | None = None,
) -> None:
    """Validate a proposal against the catalog and optionally a probed build.

    Without runtime_capabilities, validation means 'known to the catalog',
    not 'safe to execute'. The ICD candidate remains execution_authorized=False.
    When capabilities are supplied, every populated discovery dimension must
    be explicitly advertised by that exact runtime build (fail closed).
    """
    if configuration.get("runtime") != RUNTIME_ID:
        raise ValueError("configuration is not a cafe-llama.cpp configuration")

    for dimension, allowed in _DIMENSION_VOCABULARY.items():
        value = configuration.get(dimension)
        if value is not None and value not in allowed:
            raise ValueError(f"unknown cafe configuration value for {dimension}: {value!r}")
        if value is not None and runtime_capabilities is not None:
            runtime_allowed = runtime_capabilities.get(dimension, [])
            if value not in runtime_allowed:
                raise ValueError(
                    f"runtime build does not advertise {dimension}={value!r}"
                )

    flash_attention = configuration.get("flash_attention")
    if flash_attention is not None and type(flash_attention) is not bool:
        raise ValueError("flash_attention must be boolean")

    kv_cache = str(configuration.get("kv_cache") or "").lower()
    if kv_cache.startswith("turbo") and flash_attention is not True:
        raise ValueError("Turbo KV requires explicitly enabled flash attention")

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

    draft_tokens = configuration.get("draft_tokens")
    if draft_tokens is None:
        draft_tokens = 0
    if isinstance(draft_tokens, bool) or not isinstance(draft_tokens, int) or draft_tokens < 0:
        raise ValueError("draft_tokens must be a non-negative integer")

    speculation = str(configuration.get("speculation") or "none").lower()
    if speculation in {"none", "off"} and draft_tokens != 0:
        raise ValueError("non-speculative configuration cannot have draft tokens")
    if speculation not in {"none", "off"} and draft_tokens <= 0:
        raise ValueError("speculative decoding requires positive draft_tokens")


def parameter_catalog() -> dict[str, dict[str, Any]]:
    """Expose all runtime parameters, not only the small ICD sweep dimensions."""
    return {key: dict(value) for key, value in PARAMETERS.items()}


def parameter_readiness() -> dict[str, list[str]]:
    return parameters_by_readiness()


__all__ = [
    "RUNTIME_ID",
    "CAPABILITIES",
    "DISCOVERY_CAPABILITIES",
    "parameter_catalog",
    "parameter_readiness",
    "validate_cafe_configuration",
]
