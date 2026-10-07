"""Inference Configuration Discovery (ICD).

ICD sits between model/runtime compatibility and benchmark execution.
It discovers configurations to evaluate; it never executes a runtime and
never turns estimates into measurements.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable

SCHEMA_VERSION = "inference-configuration-discovery.v1"

DIMENSIONS = (
    "runtime", "runtime_revision", "kernel", "quantization", "context",
    "gpu_layers", "kv_cache", "flash_attention", "offload", "speculation",
    "draft_tokens", "batch",
)

FORBIDDEN_MEASUREMENT_FIELDS = {
    "tokens_per_second", "measured_tps", "benchmark_result", "measurement",
}


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in sorted(value.items()) if v is not None}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def configuration_signature(configuration: dict[str, Any]) -> str:
    """Return a stable identity for one executable configuration proposal."""
    payload = json.dumps(
        _clean(configuration), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_configuration(configuration: dict[str, Any]) -> None:
    """Validate the declarative ICD boundary."""
    leaked = FORBIDDEN_MEASUREMENT_FIELDS.intersection(configuration)
    if leaked:
        raise ValueError(f"configuration contains measurement fields: {sorted(leaked)}")
    if not configuration.get("runtime"):
        raise ValueError("configuration requires runtime")
    if not configuration.get("model_ref"):
        raise ValueError("configuration requires model_ref")
    if "context" in configuration and configuration["context"] is not None:
        if isinstance(configuration["context"], bool) or int(configuration["context"]) <= 0:
            raise ValueError("context must be a positive integer")
    if "draft_tokens" in configuration and configuration["draft_tokens"] is not None:
        if isinstance(configuration["draft_tokens"], bool) or int(configuration["draft_tokens"]) < 0:
            raise ValueError("draft_tokens must be a non-negative integer")


@dataclass(frozen=True)
class ConfigurationCandidate:
    configuration: dict[str, Any]
    source: str = "discovery"
    evidence_level: str = "estimated"

    def __post_init__(self) -> None:
        validate_configuration(self.configuration)

    @property
    def configuration_id(self) -> str:
        return configuration_signature(self.configuration)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "configuration_id": self.configuration_id,
            "configuration": _clean(self.configuration),
            "source": self.source,
            "evidence_level": self.evidence_level,
            "measurement_required": True,
            "execution_authorized": False,
        }


def _as_options(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return [value]


def discover_configurations(
    *,
    model: dict[str, Any],
    runtime_profiles: Iterable[dict[str, Any]],
    capabilities: dict[str, Any] | None = None,
) -> list[ConfigurationCandidate]:
    """Generate deterministic candidates from compatible runtime profiles.

    runtime_profiles remains the compatibility source of truth. ICD expands
    each profile into explicit configuration dimensions without knowing how a
    particular runtime executes them.
    """
    capabilities = capabilities or {}
    model_ref = str(model.get("model_ref") or model.get("id") or model.get("name") or "")
    if not model_ref:
        raise ValueError("model requires an id, name, or model_ref")

    candidates: list[ConfigurationCandidate] = []
    for profile in runtime_profiles:
        runtime = profile.get("runtime") or profile.get("runtime_id") or profile.get("backend")
        if not runtime:
            continue
        base = {
            "runtime": runtime,
            "runtime_revision": profile.get("runtime_revision") or profile.get("revision"),
            "kernel": profile.get("kernel"),
            "model_ref": model_ref,
            "quantization": profile.get("quantization") or model.get("quantization"),
            "context": profile.get("context_length") or model.get("context_length"),
            "gpu_layers": profile.get("gpu_layers"),
            "kv_cache": profile.get("kv_cache") or profile.get("cache"),
            "flash_attention": profile.get("flash_attention"),
            "offload": profile.get("offload"),
            "speculation": profile.get("speculation") or profile.get("spec_type"),
            "draft_tokens": profile.get("draft_tokens") or profile.get("spec_draft_n_max"),
            "batch": profile.get("batch"),
        }

        dimensions = [d for d in DIMENSIONS if d != "runtime" and d in capabilities]
        variants = [base]
        for dimension in dimensions:
            next_variants = []
            for variant in variants:
                for option in _as_options(capabilities[dimension]):
                    item = dict(variant)
                    item[dimension] = option
                    next_variants.append(item)
            variants = next_variants

        candidates.extend(
            ConfigurationCandidate(item, source="capability-discovery")
            for item in variants
        )

    unique: dict[str, ConfigurationCandidate] = {}
    for candidate in candidates:
        unique.setdefault(candidate.configuration_id, candidate)
    return list(unique.values())


def rank_measured_candidates(
    candidates: Iterable[dict[str, Any]],
    measurements: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rank configurations only when matching measured evidence exists."""
    by_id = {
        str(m.get("configuration_id")): m
        for m in measurements
        if m.get("evidence_type") == "measured"
    }
    ranked = []
    for candidate in candidates:
        cid = str(candidate.get("configuration_id") or configuration_signature(
            candidate.get("configuration", candidate)
        ))
        measurement = by_id.get(cid)
        if not measurement:
            continue
        tps = measurement.get("measured_tps")
        if not isinstance(tps, (int, float)) or isinstance(tps, bool):
            continue
        item = dict(candidate)
        item["measured_tps"] = float(tps)
        item["selection_status"] = "MEASURED"
        ranked.append(item)
    ranked.sort(key=lambda item: (-item["measured_tps"], str(item["configuration_id"])))
    return ranked
