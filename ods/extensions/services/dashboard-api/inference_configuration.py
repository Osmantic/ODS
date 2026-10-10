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
    "gpu_layers", "kv_cache", "cache_type_v", "flash_attention", "kv_unified",
    "no_kv_offload", "offload", "host_moe", "n_host_moe", "cpu_moe",
    "n_cpu_moe", "ssd_streaming", "ssd_n_streaming", "ssd_io_threads",
    "ngram_ssd", "ngram_enabled", "pipeline_parallel", "speculation",
    "draft_model", "draft_tokens", "draft_gpu_layers", "draft_cache_k",
    "draft_cache_v", "spec_draft_n_min", "spec_draft_p_min",
    "spec_draft_sampling", "draft_threads", "batch", "ubatch",
    "threads", "threads_batch", "parallel_slots", "split_mode",
    "tensor_split", "main_gpu", "safetensors_outtype", "safetensors_native",
    "mmap", "mlock", "fit", "fit_target", "fit_ctx", "rope_scaling",
    "rope_scale", "yarn_orig_ctx", "yarn_ext_factor", "yarn_attn_factor",
    "yarn_beta_slow", "yarn_beta_fast", "numa", "mmproj",
    "mmproj_offload", "mmproj_device", "image_min_tokens",
    "image_max_tokens", "video_fps", "video_timestamp_interval",
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
            "context": profile.get("context") or profile.get("context_length") or model.get("context_length"),
            "gpu_layers": profile.get("gpu_layers"),
            "kv_cache": profile.get("kv_cache") or profile.get("cache_type_k") or profile.get("cache"),
            "cache_type_v": profile.get("cache_type_v"),
            "flash_attention": profile.get("flash_attention"),
            "kv_unified": profile.get("kv_unified"),
            "no_kv_offload": profile.get("no_kv_offload"),
            "offload": profile.get("offload"),
            "host_moe": profile.get("host_moe"),
            "n_host_moe": profile.get("n_host_moe"),
            "cpu_moe": profile.get("cpu_moe"),
            "n_cpu_moe": profile.get("n_cpu_moe"),
            "ssd_streaming": profile.get("ssd_streaming"),
            "ssd_n_streaming": profile.get("ssd_n_streaming"),
            "ssd_io_threads": profile.get("ssd_io_threads"),
            "ngram_ssd": profile.get("ngram_ssd"),
            "ngram_enabled": profile.get("ngram_enabled"),
            "pipeline_parallel": profile.get("pipeline_parallel"),
            "speculation": profile.get("speculation") or profile.get("spec_type"),
            "draft_model": profile.get("draft_model"),
            "draft_tokens": profile.get("draft_tokens") if profile.get("draft_tokens") is not None else profile.get("spec_draft_n_max"),
            "draft_gpu_layers": profile.get("draft_gpu_layers") or profile.get("spec_draft_ngl"),
            "draft_cache_k": profile.get("draft_cache_k") or profile.get("spec_draft_type_k"),
            "draft_cache_v": profile.get("draft_cache_v") or profile.get("spec_draft_type_v"),
            "spec_draft_n_min": profile.get("spec_draft_n_min"),
            "spec_draft_p_min": profile.get("spec_draft_p_min"),
            "spec_draft_sampling": profile.get("spec_draft_sampling"),
            "draft_threads": profile.get("draft_threads") or profile.get("spec_draft_threads"),
            "batch": profile.get("batch"),
            "ubatch": profile.get("ubatch") or profile.get("ubatch_size"),
            "threads": profile.get("threads"),
            "threads_batch": profile.get("threads_batch"),
            "parallel_slots": profile.get("parallel_slots") or profile.get("parallel"),
            "split_mode": profile.get("split_mode"),
            "tensor_split": profile.get("tensor_split"),
            "main_gpu": profile.get("main_gpu"),
            "safetensors_outtype": profile.get("safetensors_outtype"),
            "safetensors_native": profile.get("safetensors_native"),
            "mmap": profile.get("mmap"),
            "mlock": profile.get("mlock"),
            "fit": profile.get("fit"),
            "fit_target": profile.get("fit_target"),
            "fit_ctx": profile.get("fit_ctx"),
            "rope_scaling": profile.get("rope_scaling"),
            "rope_scale": profile.get("rope_scale"),
            "yarn_orig_ctx": profile.get("yarn_orig_ctx"),
            "yarn_ext_factor": profile.get("yarn_ext_factor"),
            "yarn_attn_factor": profile.get("yarn_attn_factor"),
            "yarn_beta_slow": profile.get("yarn_beta_slow"),
            "yarn_beta_fast": profile.get("yarn_beta_fast"),
            "numa": profile.get("numa"),
            "mmproj": profile.get("mmproj"),
            "mmproj_offload": profile.get("mmproj_offload"),
            "mmproj_device": profile.get("mmproj_device"),
            "image_min_tokens": profile.get("image_min_tokens"),
            "image_max_tokens": profile.get("image_max_tokens"),
            "video_fps": profile.get("video_fps"),
            "video_timestamp_interval": profile.get("video_timestamp_interval"),
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
