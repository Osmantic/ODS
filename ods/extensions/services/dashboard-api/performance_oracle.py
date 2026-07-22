"""Evidence-first performance metadata for the dashboard model library.

The oracle is deliberately conservative:
1. measured local throughput from this machine;
2. exact published benchmark match;
3. calibrated prediction from another local measurement on this machine;
4. benchmark required.

It never returns catalog tok/s estimates as observed performance.
"""

from __future__ import annotations

import json
import os
import platform
import re
from pathlib import Path
from typing import Any, Optional

from gguf_inspector import inspect_gguf
from context_policy import HERMES_MIN_CONTEXT, HERMES_TARGET_CONTEXT
from helpers import (
    get_model_performance_samples,
    get_recorded_model_performance,
    is_plausible_single_request_tps,
)
from models import GPUInfo


_EVIDENCE_PATH = Path(__file__).with_name("performance_evidence.json")
_DEFAULT_RECOMMENDATION_POLICY = "catalog-fit-pre-download"
_VRAM_FIT_TOLERANCE_GB = 0.25
_MODEL_SELECTOR_POLICY = "context-aware-largest-capable-general-v1"
_RUNTIME_MODEL_PREFIXES = ("extra.", "user.")
_AGENT_MIN_LOCAL_TOKENS_PER_SEC = 2.0
_MODEL_PUBLISHERS = (
    (("qwen",), "Qwen", "Qwen"),
    (("phi",), "Microsoft", "microsoft"),
    (("granite",), "IBM Granite", "ibm-granite"),
    (("smollm",), "Hugging Face", "HuggingFaceTB"),
    (("gemma",), "Google", "google"),
    (("falcon",), "Technology Innovation Institute", "tiiuae"),
    (("ministral", "mistral", "mixtral"), "Mistral AI", "mistralai"),
    (("llama",), "Meta", "meta-llama"),
    (("deepseek",), "DeepSeek", "deepseek-ai"),
)


def normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def local_model_id(value: Any) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-._")
    return name or "local-gguf"


def model_publisher(model: dict[str, Any]) -> dict[str, str] | None:
    """Return the official model-family publisher used for dashboard identity."""
    explicit = model.get("publisher")
    if isinstance(explicit, dict) and explicit.get("huggingface_author"):
        return {
            "name": str(explicit.get("name") or explicit["huggingface_author"]),
            "huggingFaceAuthor": str(explicit["huggingface_author"]),
        }
    identity = normalize_key(" ".join(
        str(model.get(key) or "") for key in ("id", "name", "llm_model_name")
    ))
    identity_tokens = identity.split("-")
    for family_markers, name, author in _MODEL_PUBLISHERS:
        if any(
            token == marker
            or (token.startswith(marker) and token[len(marker):len(marker) + 1].isdigit())
            for marker in family_markers
            for token in identity_tokens
        ):
            return {"name": name, "huggingFaceAuthor": author}
    source_repo = str(model.get("source_repo") or "")
    if "/" in source_repo:
        author = source_repo.split("/", 1)[0]
        return {"name": author, "huggingFaceAuthor": author}
    return None


def _list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _normalize_host_arch(value: Any) -> str:
    key = normalize_key(value)
    if key in {"aarch64", "arm64"}:
        return "arm64"
    if key in {"x86-64", "x86_64", "amd64", "x64"}:
        return "amd64"
    return key or "unknown"


def _system_ram_gb() -> int:
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(round(stat.ullTotalPhys / (1024**3)))
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(round((pages * page_size) / (1024**3)))
    except (AttributeError, OSError, ValueError):
        return 0


def read_env_value(key: str, install_dir: str | Path) -> str:
    value = os.environ.get(key, "")
    if value:
        return value.strip().strip("\"'")
    return read_env_file_value(key, install_dir)


def read_env_file_value(key: str, install_dir: str | Path) -> str:
    env_path = Path(install_dir) / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


def read_persisted_env_value(key: str, install_dir: str | Path) -> str:
    """Read mutable install config from .env before the container environment."""
    env_path = Path(install_dir) / ".env"
    file_value = read_env_file_value(key, install_dir)
    if file_value or env_path.exists():
        return file_value
    return os.environ.get(key, "").strip().strip("\"'")


def model_files_dir(data_dir: str | Path) -> Path:
    return Path(data_dir) / "models"


def _model_aliases(model: dict[str, Any]) -> set[str]:
    aliases = {
        str(model.get("id") or ""),
        str(model.get("name") or ""),
        str(model.get("gguf") or ""),
        str(model.get("gguf_file") or ""),
        str(model.get("llm_model_name") or ""),
    }
    aliases.update(str(alias or "") for alias in model.get("aliases", []))
    for part in model.get("gguf_parts", []) or []:
        if isinstance(part, dict):
            aliases.add(str(part.get("file") or ""))
    for alias in tuple(aliases):
        path_name = re.split(r"[\\/]", alias)[-1]
        if path_name.lower().endswith(".gguf"):
            aliases.add(path_name[:-5])
    return {alias for alias in aliases if alias}


def normalize_catalog_entry(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Convert config/model-library.json entries to the oracle shape."""
    if not isinstance(raw, dict):
        return None
    gguf_parts = raw.get("gguf_parts") if isinstance(raw.get("gguf_parts"), list) else []
    gguf = raw.get("gguf") or raw.get("gguf_file")
    if not gguf and gguf_parts and isinstance(gguf_parts[0], dict):
        gguf = gguf_parts[0].get("file")

    model_id = raw.get("id") or raw.get("llm_model_name") or raw.get("name") or gguf
    if not model_id or not gguf:
        return None

    try:
        size_mb = float(raw.get("size_mb") or (float(raw.get("sizeGb", 0)) * 1024))
    except (TypeError, ValueError):
        size_mb = 0.0
    try:
        vram_required = float(raw.get("vram_required_gb") or raw.get("vramRequired") or 0)
    except (TypeError, ValueError):
        vram_required = 0.0
    try:
        context_length = int(raw.get("context_length") or raw.get("contextLength") or 0)
    except (TypeError, ValueError):
        context_length = 0

    aliases = set(_model_aliases(raw))
    if raw.get("llm_model_name"):
        aliases.add(str(raw["llm_model_name"]))

    model = {
        "id": str(model_id),
        "name": raw.get("name") or str(model_id),
        "family": raw.get("family"),
        "gguf": str(gguf),
        "gguf_file": str(gguf),
        "gguf_url": raw.get("gguf_url", ""),
        "gguf_sha256": raw.get("gguf_sha256", ""),
        "gguf_parts": gguf_parts,
        "llm_model_name": raw.get("llm_model_name"),
        "llama_server_image": raw.get("llama_server_image"),
        "size_mb": size_mb,
        "vram_required_gb": vram_required,
        "context_length": context_length,
        "specialty": raw.get("specialty", "General"),
        "description": raw.get("description", ""),
        "quantization": raw.get("quantization"),
        "architecture": raw.get("architecture", "dense"),
        "active_params_b": raw.get("active_params_b"),
        "tokens_per_sec_estimate": raw.get("tokens_per_sec_estimate"),
        "runtime_profiles": raw.get("runtime_profiles") if isinstance(raw.get("runtime_profiles"), list) else [],
        "app_compatibility": raw.get("app_compatibility") if isinstance(raw.get("app_compatibility"), dict) else {},
        "catalog_source": raw.get("source") or "ods",
        "source_repo": raw.get("source_repo"),
        "source_revision": raw.get("source_revision"),
        "source_url": raw.get("source_url"),
        "license": raw.get("license"),
        "imported_at": raw.get("imported_at"),
        "context_source": raw.get("context_source"),
        "aliases": sorted(aliases),
    }
    if raw.get("decode_read_mb"):
        model["decode_read_mb"] = raw["decode_read_mb"]
    return model


def _scope_values(raw: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        if key in raw:
            return [normalize_key(item) for item in _list_value(raw.get(key)) if normalize_key(item)]
    return []


def model_compatibility_runtime_context(
    install_dir: str | Path | None = None,
    gpu_info: Optional[GPUInfo] = None,
    runtime: str | None = None,
) -> dict[str, Any]:
    def runtime_value(key: str) -> str:
        if install_dir is not None:
            value = read_env_file_value(key, install_dir) or read_env_value(key, install_dir)
            if value:
                return value
        return os.environ.get(key, "")

    llm_backend = runtime or runtime_value("LLM_BACKEND")
    gpu_backend = (
        getattr(gpu_info, "gpu_backend", None)
        or runtime_value("GPU_BACKEND")
    )
    explicit_host_values = {
        normalize_key(value)
        for value in (
            runtime_value("ODS_FLEET_HOST_ID"),
            runtime_value("ODS_COMPATIBILITY_HOST"),
        )
        if normalize_key(value)
    }
    host_values = explicit_host_values or {
        normalize_key(value)
        for value in (
            runtime_value("ODS_DEVICE_NAME"),
            runtime_value("COMPUTERNAME"),
            runtime_value("HOSTNAME"),
            platform.node(),
        )
        if normalize_key(value)
    }
    return {
        "llmBackend": normalize_key(llm_backend),
        "runtime": normalize_key(llm_backend),
        "gpuBackend": normalize_key(gpu_backend),
        "odsMode": normalize_key(runtime_value("ODS_MODE")),
        "host": sorted(host_values)[0] if host_values else "",
        "hosts": sorted(host_values),
    }


def _context_values(context: dict[str, Any], *keys: str) -> set[str]:
    values: set[str] = set()
    for key in keys:
        value = context.get(key)
        if isinstance(value, (list, tuple, set)):
            for item in value:
                normalized = normalize_key(item)
                if normalized:
                    values.add(normalized)
            continue
        normalized = normalize_key(value)
        if normalized:
            values.add(normalized)
    return values


def _compatibility_scope_matches(raw: Any, runtime_context: Optional[dict[str, Any]]) -> bool:
    if not isinstance(raw, dict):
        return True
    context = runtime_context or model_compatibility_runtime_context()
    checks = [
        (_scope_values(raw, "llmBackendScope", "llm_backend_scope", "runtimeScope", "runtime_scope"),
         _context_values(context, "llmBackend", "runtime")),
        (_scope_values(raw, "gpuBackendScope", "gpu_backend_scope"),
         _context_values(context, "gpuBackend")),
        (_scope_values(raw, "odsModeScope", "ods_mode_scope"),
         _context_values(context, "odsMode")),
        (_scope_values(raw, "hostScope", "host_scope", "fleetHostScope", "fleet_host_scope"),
         _context_values(context, "host", "hosts")),
    ]
    for allowed, actual_values in checks:
        if allowed and not (set(allowed) & {value for value in actual_values if value}):
            return False
    return True


def _app_compatibility_entry(
    raw: Any,
    default_label: str,
    runtime_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if isinstance(raw, dict) and not _compatibility_scope_matches(raw, runtime_context):
        return {
            "status": "unknown",
            "label": default_label,
            "reason": "",
        }

    if isinstance(raw, dict):
        status = str(raw.get("status") or "unknown").strip() or "unknown"
        label = str(raw.get("label") or default_label).strip() or default_label
        reason = str(raw.get("reason") or "").strip()
        evidence = str(raw.get("evidence") or "").strip()
    elif isinstance(raw, str) and raw.strip():
        status = raw.strip()
        label = default_label
        reason = ""
        evidence = ""
    else:
        status = "unknown"
        label = default_label
        reason = ""
        evidence = ""

    payload = {
        "status": normalize_key(status).replace("-", "_") or "unknown",
        "label": label,
        "reason": reason,
    }
    if evidence:
        payload["evidence"] = evidence
    return payload


def _app_compatibility_payload_key(key: Any) -> str:
    raw = normalize_key(str(key or ""))
    if not raw:
        return ""
    aliases = {
        "agent_viability": "agentViability",
        "hermes_talk": "hermesTalk",
        "openai_chat": "openaiChat",
    }
    if raw in aliases:
        return aliases[raw]
    parts = [part for part in raw.split("_") if part]
    if not parts:
        return ""
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _app_compatibility_default_label(key: Any) -> str:
    parts = [part for part in normalize_key(str(key or "")).split("_") if part]
    if not parts:
        return "App compatibility untested"
    acronyms = {"api": "API", "llm": "LLM", "ui": "UI"}
    label = " ".join(acronyms.get(part, part[:1].upper() + part[1:]) for part in parts)
    return f"{label} untested"


def _exact_performance_agent_block(performance: Optional[dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(performance, dict):
        return None
    if performance.get("source") not in {"measured_local", "published_exact"}:
        return None
    try:
        tokens_per_sec = float(performance.get("tokensPerSec") or 0)
    except (TypeError, ValueError):
        return None
    if tokens_per_sec <= 0 or tokens_per_sec >= _AGENT_MIN_LOCAL_TOKENS_PER_SEC:
        return None

    return {
        "tokensPerSec": round(tokens_per_sec, 1),
        "reason": (
            f"Local measured throughput is {tokens_per_sec:.1f} tok/s, below the "
            f"{_AGENT_MIN_LOCAL_TOKENS_PER_SEC:.1f} tok/s floor for ODS Talk and "
            "agent-required workflows on this machine."
        ),
    }


def model_app_compatibility(
    model: dict[str, Any],
    performance: Optional[dict[str, Any]] = None,
    runtime_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    raw = model.get("app_compatibility") if isinstance(model.get("app_compatibility"), dict) else {}
    hermes_talk = _app_compatibility_entry(raw.get("hermes_talk"), "ODS Talk untested", runtime_context)
    compatibility = {
        "openaiChat": _app_compatibility_entry(raw.get("openai_chat"), "Direct chat untested", runtime_context),
        "hermesTalk": hermes_talk,
        "agentViability": _agent_viability_entry(raw.get("agent_viability"), hermes_talk, runtime_context),
    }
    for raw_key, raw_value in raw.items():
        payload_key = _app_compatibility_payload_key(raw_key)
        if not payload_key or payload_key in compatibility:
            continue
        compatibility[payload_key] = _app_compatibility_entry(
            raw_value,
            _app_compatibility_default_label(raw_key),
            runtime_context,
        )
    exact_speed_block = _exact_performance_agent_block(performance)
    if exact_speed_block:
        compatibility["hermesTalk"] = {
            "status": "unsupported_until_revalidated",
            "label": "Too slow for ODS Talk",
            "reason": exact_speed_block["reason"],
        }
        compatibility["agentViability"] = {
            "status": "not_agent_viable",
            "label": "Too slow for agents",
            "reason": exact_speed_block["reason"],
        }
    return compatibility


def _agent_viability_entry(
    raw: Any,
    hermes_talk: dict[str, Any],
    runtime_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if raw:
        return _app_compatibility_entry(raw, "Agent viability untested", runtime_context)

    hermes_status = str((hermes_talk or {}).get("status") or "unknown").strip().lower()
    hermes_reason = str((hermes_talk or {}).get("reason") or "").strip()
    hermes_evidence = str((hermes_talk or {}).get("evidence") or "").strip()
    if hermes_status in {
        "blocked",
        "incompatible",
        "not_recommended",
        "not_supported",
        "unsupported",
        "unsupported_until_revalidated",
    }:
        payload = {
            "status": "not_agent_viable",
            "label": "Agent viability blocked",
            "reason": hermes_reason or "This model is not currently viable for agent-required ODS workflows.",
        }
        if hermes_evidence:
            payload["evidence"] = hermes_evidence
        return payload
    if hermes_status in {"supported", "verified"}:
        payload = {
            "status": "agent_viable",
            "label": "Agent viable",
            "reason": hermes_reason,
        }
        if hermes_evidence:
            payload["evidence"] = hermes_evidence
        return payload
    return {
        "status": "unknown",
        "label": "Agent viability untested",
        "reason": "",
    }


def load_model_catalog(install_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(install_dir) / "config" / "model-library.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    raw_models = data.get("models", [])
    if not isinstance(raw_models, list):
        return []

    return [
        model
        for model in (normalize_catalog_entry(raw) for raw in raw_models)
        if model is not None
    ]


def collect_runtime_flags(install_dir: str | Path) -> dict[str, str]:
    mapping = {
        "LLAMA_ARG_N_CPU_MOE": "n_cpu_moe",
        "LLAMA_N_CPU_MOE": "n_cpu_moe",
        "LLAMA_ARG_CACHE_TYPE_K": "cache_type_k",
        "LLAMA_CACHE_TYPE_K": "cache_type_k",
        "LLAMA_ARG_CACHE_TYPE_V": "cache_type_v",
        "LLAMA_CACHE_TYPE_V": "cache_type_v",
        "LLAMA_ARG_FLASH_ATTN": "flash_attn",
        "LLAMA_FLASH_ATTN": "flash_attn",
        "LLAMA_ARG_CHECKPOINT_EVERY_N_TOKENS": "checkpoint_every_n_tokens",
        "LLAMA_CHECKPOINT_EVERY_N_TOKENS": "checkpoint_every_n_tokens",
        "LLAMA_ARG_NO_CACHE_PROMPT": "no_cache_prompt",
        "LLAMA_NO_CACHE_PROMPT": "no_cache_prompt",
    }
    flags: dict[str, str] = {}
    for env_name, canonical in mapping.items():
        value = read_env_value(env_name, install_dir)
        if value and canonical not in flags:
            flags[canonical] = value
    return flags


def load_evidence(path: Path = _EVIDENCE_PATH) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = data.get("entries", [])
    return entries if isinstance(entries, list) else []


def format_size(size_mb: int | float) -> str:
    gb = float(size_mb) / 1024
    return f"{gb:.1f} GB" if gb >= 1 else f"{int(size_mb)} MB"


def _runtime_model_aliases(value: Any) -> set[str]:
    token = str(value or "").strip()
    if not token:
        return set()

    aliases = {token}
    basename = re.split(r"[\\/]", token)[-1]
    if basename:
        aliases.add(basename)

    for alias in tuple(aliases):
        lower_alias = alias.lower()
        for prefix in _RUNTIME_MODEL_PREFIXES:
            if lower_alias.startswith(prefix):
                aliases.add(alias[len(prefix):])
                break
    return aliases


def current_model_matches(model: dict[str, Any], current_model: str | None, current_gguf: str | None = None) -> bool:
    model_keys = {normalize_key(alias) for alias in _model_aliases(model)}
    runtime_keys = {
        normalize_key(alias)
        for value in (current_model, current_gguf)
        for alias in _runtime_model_aliases(value)
    }
    model_keys.discard("")
    runtime_keys.discard("")
    return bool(model_keys & runtime_keys)


def find_catalog_model(catalog: list[dict[str, Any]], model_name: str | None, gguf: str | None = None) -> dict[str, Any] | None:
    if not model_name and not gguf:
        return None
    return next((model for model in catalog if current_model_matches(model, model_name, gguf)), None)


def _hardware_match(gpu_info: Optional[GPUInfo], context_length: Optional[int],
                    quantization: str | None, runtime: str | None = None) -> dict[str, Any]:
    if not gpu_info:
        return {
            "backend": "unknown",
            "gpu": None,
            "vramGb": None,
            "contextLength": context_length,
            "quantization": quantization,
            "runtime": runtime,
        }
    return {
        "backend": gpu_info.gpu_backend,
        "gpu": gpu_info.name,
        "vramGb": round(gpu_info.memory_total_mb / 1024, 1),
        "contextLength": context_length,
        "quantization": quantization,
        "runtime": runtime,
    }


def _default_performance(source: str, label: str, hardware_match: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": source,
        "label": label,
        "tokensPerSec": None,
        "low": None,
        "high": None,
        "confidence": "none",
        "sampleCount": 0,
        "sourceUrl": None,
        "hardwareMatch": hardware_match,
    }


def _fits_declared_vram(required_gb: float, capacity_gb: float) -> bool:
    """Compare catalog VRAM requirements against detected memory.

    GPU vendors report memory in MiB and marketing specs in rounded GB. A card
    sold as 8GB commonly reports slightly below 8.0GiB, so keep a small fixed
    tolerance to avoid marking exact-tier models as incompatible.
    """
    return required_gb <= capacity_gb + _VRAM_FIT_TOLERANCE_GB


def _estimated_param_billions(model: dict[str, Any]) -> float:
    """Best-effort model scale from explicit metadata, name, then file size.

    The selector needs this before a GGUF exists locally. Catalog file size is
    still authoritative for disk/download size; this estimate is only for
    context/KV memory pressure.
    """
    for key in ("total_params_b", "params_b"):
        try:
            value = float(model.get(key) or 0)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass

    numbers = []
    for text in (model.get("id"), model.get("name"), model.get("llm_model_name"), model.get("gguf")):
        numbers.extend(float(match) for match in re.findall(r"(\d+(?:\.\d+)?)\s*b", str(text or ""), re.I))
    if numbers:
        return max(numbers)

    size_mb = float(model.get("size_mb") or 0)
    if size_mb > 0:
        # Q4_K_M GGUFs are roughly 0.55-0.65 GiB per billion params. Use the
        # middle so unknown compact models still get a realistic KV estimate.
        return max(size_mb / 600.0, 1.0)
    return 4.0


def _estimated_context_kv_gb(model: dict[str, Any]) -> float:
    context = max(int(model.get("context_length") or 0), 8192)
    params_b = _estimated_param_billions(model)
    # KV cache is architecture-dependent, but the catalog does not always carry
    # hidden size/layer metadata before download. This intentionally estimates
    # standard llama.cpp KV pressure for the requested context and lets published
    # runtime-specific evidence (TurboQuant/DFlash/etc.) override performance,
    # not baseline install compatibility.
    kv_per_32k_gb = min(max(params_b * 0.12, 0.35), 3.5)
    return round(kv_per_32k_gb * (context / 32768.0), 2)


def _selector_required_memory_gb(model: dict[str, Any]) -> float:
    declared = float(model.get("vram_required_gb") or 0)
    size_gb = float(model.get("size_mb") or 0) / 1024.0
    context_kv_gb = _estimated_context_kv_gb(model)
    if size_gb <= 0:
        return round(declared, 2)
    return round(max(declared, size_gb + context_kv_gb), 2)


def _matching_runtime_profile(model: dict[str, Any], gpu_info: Optional[GPUInfo],
                              system_ram_gb: int | None = None) -> dict[str, Any] | None:
    if not gpu_info:
        return None
    backend = normalize_key(gpu_info.gpu_backend)
    memory_type = (
        "unified"
        if backend == "apple"
        or normalize_key(getattr(gpu_info, "memory_type", "")) == "unified"
        or "strix-halo" in normalize_key(gpu_info.name)
        else "discrete"
    )
    host_arch = _normalize_host_arch(platform.machine())
    vram_gb = float(gpu_info.memory_total_mb or 0) / 1024.0
    ram_gb = system_ram_gb if system_ram_gb is not None else _system_ram_gb()
    for profile in model.get("runtime_profiles", []) or []:
        if not isinstance(profile, dict):
            continue
        if normalize_key(profile.get("backend")) not in {"", backend}:
            continue
        allowed_arches = {_normalize_host_arch(item) for item in _list_value(profile.get("host_arch"))}
        if allowed_arches and host_arch not in allowed_arches:
            continue
        required_memory_type = normalize_key(profile.get("memory_type"))
        if required_memory_type and required_memory_type != memory_type:
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


def _effective_context_length(model: dict[str, Any], runtime_profile: dict[str, Any] | None = None) -> int:
    if runtime_profile and runtime_profile.get("context_length"):
        return int(runtime_profile["context_length"])
    return int(model.get("context_length") or 0)


def _effective_required_memory_gb(model: dict[str, Any],
                                  runtime_profile: dict[str, Any] | None = None) -> float:
    if runtime_profile and runtime_profile.get("estimated_required_gb") is not None:
        return round(float(runtime_profile["estimated_required_gb"]), 2)
    if runtime_profile and runtime_profile.get("context_length"):
        model = {**model, "context_length": int(runtime_profile["context_length"])}
    return _selector_required_memory_gb(model)


def _usable_model_memory_gb(gpu_info: Optional[GPUInfo]) -> float:
    if not gpu_info:
        return 0.0
    total_gb = gpu_info.memory_total_mb / 1024
    backend = normalize_key(gpu_info.gpu_backend)
    if backend == "apple" or "strix-halo" in normalize_key(gpu_info.name):
        return max(total_gb * 0.55, 2.0)
    return total_gb


def _tokens_performance(source: str, label: str, tokens_per_second: float, confidence: str,
                        hardware_match: dict[str, Any], sample_count: int = 1,
                        source_url: str | None = None, spread: float = 0.1) -> dict[str, Any]:
    tps = round(float(tokens_per_second), 1)
    low = round(tps * (1 - spread), 1)
    high = round(tps * (1 + spread), 1)
    return {
        "source": source,
        "label": label.format(tps=tps, low=low, high=high),
        "tokensPerSec": tps,
        "low": low,
        "high": high,
        "confidence": confidence,
        "sampleCount": int(sample_count or 0),
        "sourceUrl": source_url,
        "hardwareMatch": hardware_match,
    }


def _sample_tps(sample: Optional[dict[str, Any]]) -> Optional[float]:
    if not sample:
        return None
    try:
        tps = float(sample.get("tokens_per_second") or sample.get("tokensPerSecond") or 0)
    except (TypeError, ValueError):
        return None
    return tps if is_plausible_single_request_tps(tps) else None


def _exact_sample(model: dict[str, Any], gpu_info: Optional[GPUInfo], context_length: Optional[int]) -> Optional[dict[str, Any]]:
    if not gpu_info:
        return None
    for alias in _model_aliases(model):
        sample = get_recorded_model_performance(
            alias,
            gpu_info.name,
            gpu_info.gpu_backend,
            context_length=context_length,
            gguf=model.get("gguf"),
            vram_total_mb=gpu_info.memory_total_mb,
        )
        if sample and _sample_tps(sample) is not None:
            return sample
    return None


def _published_exact(model: dict[str, Any], gpu_info: Optional[GPUInfo], context_length: Optional[int],
                     quantization: str | None, flags: dict[str, str],
                     runtime: str | None,
                     evidence: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not gpu_info or not context_length:
        return None
    model_keys = {normalize_key(alias) for alias in _model_aliases(model)}
    for entry in evidence:
        entry_names = {normalize_key(entry.get("model_id"))}
        entry_names.update(normalize_key(v) for v in entry.get("model_names", []))
        if not model_keys.intersection(entry_names):
            continue
        if normalize_key(entry.get("backend")) != normalize_key(gpu_info.gpu_backend):
            continue
        if normalize_key(entry.get("gpu_name")) != normalize_key(gpu_info.name):
            continue
        if int(round(float(entry.get("vram_gb", 0)))) != int(round(gpu_info.memory_total_mb / 1024)):
            continue
        if int(entry.get("context_length", 0)) != int(context_length):
            continue
        if normalize_key(entry.get("quantization")) != normalize_key(quantization):
            continue
        entry_runtime = normalize_key(entry.get("runtime"))
        current_runtime = normalize_key(runtime)
        if entry_runtime and current_runtime and current_runtime not in entry_runtime and entry_runtime not in current_runtime:
            continue
        required_flags = entry.get("flags") or {}
        if any(normalize_key(flags.get(k)) != normalize_key(v) for k, v in required_flags.items()):
            continue
        return entry
    return None


def _best_calibration_sample(gpu_info: Optional[GPUInfo]) -> Optional[dict[str, Any]]:
    if not gpu_info:
        return None
    best = None
    for sample in get_model_performance_samples():
        if normalize_key(sample.get("backend")) != normalize_key(gpu_info.gpu_backend):
            continue
        if normalize_key(sample.get("gpu")) != normalize_key(gpu_info.name):
            continue
        if sample.get("vram_total_mb") and abs(float(sample["vram_total_mb"]) - gpu_info.memory_total_mb) > 1024:
            continue
        if _sample_tps(sample) is None:
            continue
        if best is None or int(sample.get("sample_count", 0)) > int(best.get("sample_count", 0)):
            best = sample
    return best


def _model_decode_mb(model: dict[str, Any], metadata: dict[str, Any]) -> float:
    if model.get("decode_read_mb"):
        return float(model["decode_read_mb"])
    if metadata.get("size_bytes"):
        return max(float(metadata["size_bytes"]) / (1024 * 1024), 1.0)
    if model.get("sizeGb"):
        return max(float(model["sizeGb"]) * 1024, 1.0)
    return max(float(model.get("size_mb") or 1), 1.0)


def _predicted_from_calibration(model: dict[str, Any], metadata: dict[str, Any],
                                calibration: Optional[dict[str, Any]],
                                hardware_match: dict[str, Any]) -> Optional[dict[str, Any]]:
    base_tps = _sample_tps(calibration)
    if not calibration or not base_tps:
        return None
    calibration_mb = float(calibration.get("decode_read_mb") or calibration.get("model_size_mb") or 0)
    if calibration_mb <= 0:
        return None
    target_mb = _model_decode_mb(model, metadata)
    predicted = max(base_tps * (calibration_mb / target_mb), 1.0)
    if not is_plausible_single_request_tps(predicted):
        return None
    count = int(calibration.get("sample_count", 1))
    confidence = "medium" if count >= 3 else "low"
    spread = 0.2 if confidence == "medium" else 0.35
    return _tokens_performance(
        "predicted_calibrated",
        "{low}-{high} tok/s calibrated",
        predicted,
        confidence,
        hardware_match,
        sample_count=count,
        spread=spread,
    )


def evaluate_performance(model: dict[str, Any], gpu_info: Optional[GPUInfo], metadata: dict[str, Any],
                         is_loaded: bool, live_tps: float, context_length: Optional[int],
                         flags: dict[str, str], evidence: list[dict[str, Any]],
                         fits_total: bool, runtime: str | None = None) -> dict[str, Any]:
    quantization = metadata.get("quantization")
    if not quantization or quantization == "unknown":
        quantization = model.get("quantization")
    hardware_match = _hardware_match(gpu_info, context_length, quantization, runtime)

    if gpu_info and not fits_total and not is_loaded:
        return _default_performance("incompatible", "does not fit this GPU", hardware_match)

    live_sample_tps = _sample_tps({"tokens_per_second": live_tps})
    if is_loaded and live_sample_tps is not None:
        return _tokens_performance(
            "measured_local",
            "{tps} tok/s measured locally",
            live_sample_tps,
            "high",
            hardware_match,
            sample_count=1,
        )

    sample = _exact_sample(model, gpu_info, context_length)
    tps = _sample_tps(sample)
    if tps is not None:
        return _tokens_performance(
            "measured_local",
            "{tps} tok/s measured locally",
            tps,
            "high",
            hardware_match,
            sample_count=int(sample.get("sample_count", 1)),
        )

    published = _published_exact(model, gpu_info, context_length, quantization, flags, runtime, evidence)
    published_tps = _sample_tps(published)
    if published and published_tps is not None:
        return _tokens_performance(
            "published_exact",
            "{tps} tok/s published exact",
            published_tps,
            "medium",
            hardware_match,
            sample_count=1,
            source_url=published.get("source_url"),
            spread=0.05,
        )

    prediction = _predicted_from_calibration(model, metadata, _best_calibration_sample(gpu_info), hardware_match)
    if prediction:
        return prediction

    return _default_performance("benchmark_required", "benchmark required", hardware_match)


def build_sample_signature(model: dict[str, Any], gpu_info: Optional[GPUInfo],
                           context_length: Optional[int], install_dir: str | Path,
                           gguf_path: Path | None = None) -> dict[str, Any]:
    metadata = inspect_gguf(gguf_path) if gguf_path else {}
    return {
        "model_id": model.get("id"),
        "gguf": model.get("gguf"),
        "quantization": metadata.get("quantization") if metadata.get("quantization") != "unknown" else model.get("quantization"),
        "architecture": metadata.get("architecture") or model.get("architecture", "unknown"),
        "context_length": context_length or model.get("context_length"),
        "decode_read_mb": _model_decode_mb(model, metadata),
        "backend": gpu_info.gpu_backend if gpu_info else "unknown",
        "gpu": gpu_info.name if gpu_info else None,
        "vram_total_mb": gpu_info.memory_total_mb if gpu_info else None,
        "os": platform.platform(),
        "flags": collect_runtime_flags(install_dir),
    }


def _recommendation_from_env(install_dir: str | Path) -> dict[str, Any]:
    recommended_context = read_persisted_env_value("MODEL_RECOMMENDED_CONTEXT", install_dir)
    return {
        "source": read_persisted_env_value("MODEL_RECOMMENDATION_SOURCE", install_dir) or "installer_configured",
        "confidence": read_persisted_env_value("MODEL_RECOMMENDATION_CONFIDENCE", install_dir) or "medium",
        "reason": read_persisted_env_value("MODEL_RECOMMENDATION_REASON", install_dir) or "",
        "performanceSource": read_persisted_env_value("MODEL_PERFORMANCE_SOURCE", install_dir) or "benchmark_required",
        "performanceLabel": read_persisted_env_value("MODEL_PERFORMANCE_LABEL", install_dir) or "Benchmark after first launch",
        "model": read_persisted_env_value("MODEL_RECOMMENDED_MODEL", install_dir) or read_persisted_env_value("LLM_MODEL", install_dir) or None,
        "gguf": read_persisted_env_value("MODEL_RECOMMENDED_GGUF", install_dir) or read_persisted_env_value("GGUF_FILE", install_dir) or None,
        "contextLength": int(recommended_context) if str(recommended_context).isdigit() else None,
        "selectionPolicy": read_persisted_env_value("MODEL_RECOMMENDATION_POLICY", install_dir) or _DEFAULT_RECOMMENDATION_POLICY,
    }


def _host_amd_runtime_gpu_from_env(install_dir: str | Path, system_ram_gb: int) -> Optional[GPUInfo]:
    """Build a conservative GPU surrogate for Windows AMD native runtimes.

    On Windows AMD installs, dashboard-api runs inside Docker while Lemonade or
    llama-server runs on the host. The container often cannot inspect the host
    APU/GPU directly, so get_gpu_info() can be None even though the host runtime
    can load a downloaded model. Use the installer-written runtime contract
    instead of falling back to an artificial 4GB compatibility ceiling.
    """
    def runtime_value(key: str) -> str:
        return read_env_file_value(key, install_dir) or os.environ.get(key, "")

    gpu_backend = normalize_key(runtime_value("GPU_BACKEND"))
    if gpu_backend != "amd" or system_ram_gb <= 0:
        return None

    location = normalize_key(runtime_value("AMD_INFERENCE_LOCATION"))
    runtime = normalize_key(runtime_value("AMD_INFERENCE_RUNTIME"))
    llm_backend = normalize_key(runtime_value("LLM_BACKEND"))
    managed = normalize_key(runtime_value("AMD_INFERENCE_MANAGED"))
    if location not in {"host", "local", ""}:
        return None
    if runtime not in {"lemonade", "llama-server", ""} and llm_backend != "lemonade":
        return None
    if managed in {"false", "no", "off"} and not runtime and llm_backend != "lemonade":
        return None
    profile_text = normalize_key(" ".join([
        runtime_value("MODEL_RUNTIME_PROFILE"),
        runtime_value("MODEL_RUNTIME_PROFILE_LABEL"),
        runtime_value("MODEL_RECOMMENDATION_POLICY"),
        runtime_value("MODEL_RECOMMENDATION_REASON"),
    ]))
    if not any(marker in profile_text for marker in ("strix", "unified-memory", "unified")):
        return None

    total_mb = int(system_ram_gb * 1024)
    return GPUInfo(
        name="AMD Strix Halo host runtime",
        memory_used_mb=0,
        memory_total_mb=total_mb,
        memory_percent=0,
        utilization_percent=0,
        temperature_c=0,
        memory_type="unified",
        gpu_backend="amd",
    )


def _catalog_fit_reason(model: dict[str, Any], gpu_info: Optional[GPUInfo], configured: bool) -> str:
    runtime_profile = model.get("_runtime_profile") if isinstance(model.get("_runtime_profile"), dict) else None
    context_k = int(_effective_context_length(model, runtime_profile) / 1024) if _effective_context_length(model, runtime_profile) else 0
    required = _effective_required_memory_gb(model, runtime_profile)
    if runtime_profile:
        prefix = "Selected by the installer" if configured else "Recommended from catalog runtime profile"
        label = runtime_profile.get("label") or runtime_profile.get("id") or "advanced runtime profile"
        return (
            f"{prefix}: {model['name']} uses {label}, needs about {required:g}GB GPU headroom "
            f"plus {runtime_profile.get('system_ram_min_gb', 'documented')}GB system RAM, "
            f"and provides {context_k}K context. Benchmark locally after first launch."
        )
    if gpu_info:
        detected = round(gpu_info.memory_total_mb / 1024, 1)
        usable = round(_usable_model_memory_gb(gpu_info), 1)
        if usable < detected:
            basis = f"{usable}GB usable {gpu_info.gpu_backend.upper()} memory ({detected}GB detected)"
        else:
            basis = f"{detected}GB {gpu_info.gpu_backend.upper()} memory"
    else:
        basis = "detected local hardware"
    prefix = "Selected by the installer" if configured else "Recommended from catalog fit"
    return (
        f"{prefix}: {model['name']} needs about {required:g}GB including context/KV, "
        f"fits {basis}, and provides {context_k}K context. Benchmark locally after first launch."
    )


def _model_profile(install_dir: str | Path | None = None, explicit_profile: str | None = None) -> str:
    profile = explicit_profile or (read_env_value("MODEL_PROFILE", install_dir) if install_dir else "") or "qwen"
    normalized = normalize_key(profile)
    if normalized in {"gemma", "gemma4", "gemma-4"}:
        return "gemma4"
    if normalized == "auto":
        return "auto"
    return "qwen"


def _family_allowed_for_profile(model: dict[str, Any], profile: str) -> bool:
    family = normalize_key(model.get("family"))
    if profile == "gemma4":
        # Keep the tiny Qwen bootstrap fallback available for the minimum tier,
        # but otherwise honor the Gemma profile choice.
        return family == "gemma4" or model.get("id") == "qwen3.5-2b-q4"
    # The default profile is the broad open-model lane. It keeps Gemma out so a
    # user who explicitly wants Gemma does not get a Qwen-family recommendation
    # and vice versa, while still allowing Phi/DeepSeek entries in the catalog.
    return family != "gemma4"


def _recommendation_score(model: dict[str, Any], capacity_gb: float, profile: str) -> float:
    runtime_profile = model.get("_runtime_profile") if isinstance(model.get("_runtime_profile"), dict) else None
    required = _effective_required_memory_gb(model, runtime_profile)
    size_mb = max(float(model.get("size_mb") or 1), 1.0)
    context = max(_effective_context_length(model, runtime_profile), 8192)
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
    family_bonus = 0.35 if (profile == "gemma4" and family == "gemma4") else 0.0
    family_bonus += 0.25 if (profile in {"qwen", "auto"} and family == "qwen") else 0.0
    context_bonus = min(context / 32768, 4.0) * 0.18
    capability = min(size_mb / 1024, 48.0) * 0.24

    # Exact-tier cards are valid, but prefer a little headroom when two models
    # are otherwise similar. This prevents a 4GB card from picking a 4GB model
    # over a nearly-equivalent 3GB option while still allowing 8GB-class picks.
    fit_ratio = required / max(capacity_gb, 1.0)
    headroom_penalty = 0.0
    if fit_ratio > 0.98:
        headroom_penalty = 0.35
    elif fit_ratio > 0.92:
        headroom_penalty = 0.15

    return specialty_weight + family_bonus + context_bonus + capability - headroom_penalty


def rank_pre_download_models(catalog: list[dict[str, Any]], gpu_info: Optional[GPUInfo],
                             profile: str = "qwen", installable_only: bool = False,
                             limit: int = 3, system_ram_gb: int | None = None) -> list[dict[str, Any]]:
    """Rank catalog entries before any model is installed.

    The ranker uses only compatibility metadata from model-library.json and the
    detected hardware envelope. It intentionally does not turn catalog tok/s
    estimates into displayed performance.
    """
    if not catalog:
        return []

    normalized_profile = _model_profile(explicit_profile=profile)
    capacity_gb = _usable_model_memory_gb(gpu_info) if gpu_info else 4.0

    candidates = []
    for model in catalog:
        if installable_only and not model.get("gguf_url"):
            continue
        if not _family_allowed_for_profile(model, normalized_profile):
            continue
        runtime_profile = _matching_runtime_profile(model, gpu_info, system_ram_gb)
        candidate_model = {**model, "_runtime_profile": runtime_profile} if runtime_profile else model
        required = _effective_required_memory_gb(candidate_model, runtime_profile)
        fits = _fits_declared_vram(required, capacity_gb)
        if not fits:
            continue
        candidates.append({
            "model": candidate_model,
            "score": _recommendation_score(candidate_model, capacity_gb or max(required, 1.0), normalized_profile),
        })

    if not candidates:
        fallback_pool = [
            model for model in catalog
            if (not installable_only or model.get("gguf_url")) and _family_allowed_for_profile(model, normalized_profile)
        ] or catalog
        fallback = min(fallback_pool, key=lambda m: float(m.get("vram_required_gb") or 999))
        candidates = [{"model": fallback, "score": -1.0}]

    ranked = sorted(
        candidates,
        key=lambda item: (
            item["score"],
            _effective_required_memory_gb(item["model"], item["model"].get("_runtime_profile")),
            _effective_context_length(item["model"], item["model"].get("_runtime_profile")),
        ),
        reverse=True,
    )
    return [item["model"] for item in ranked[:max(limit, 1)]]


def select_pre_download_model(catalog: list[dict[str, Any]], gpu_info: Optional[GPUInfo]) -> dict[str, Any] | None:
    """Select the best catalog candidate when no installer choice exists.

    This uses the project-maintained `vram_required_gb` compatibility field,
    then prefers larger capable models and longer context. It is a fit
    recommendation, not a performance estimate.
    """
    ranked = rank_pre_download_models(catalog, gpu_info, profile="qwen", limit=1)
    return ranked[0] if ranked else None


def _recommendation_alternative(model: dict[str, Any], gpu_info: Optional[GPUInfo]) -> dict[str, Any]:
    runtime_profile = model.get("_runtime_profile") if isinstance(model.get("_runtime_profile"), dict) else _matching_runtime_profile(model, gpu_info)
    context = _effective_context_length(model, runtime_profile)
    vram_required = float(model.get("vram_required_gb") or 0)
    selector_required = _effective_required_memory_gb(model, runtime_profile)
    return {
        "id": model.get("id"),
        "name": model.get("name"),
        "model": model.get("llm_model_name") or model.get("id"),
        "gguf": model.get("gguf"),
        "vramRequired": vram_required,
        "estimatedRequired": selector_required,
        "contextLength": context,
        "specialty": model.get("specialty"),
        "runtimeProfile": runtime_profile.get("id") if runtime_profile else None,
        "fitsVram": _fits_declared_vram(selector_required, _usable_model_memory_gb(gpu_info) if gpu_info else 4.0),
        "reason": _catalog_fit_reason({**model, "_runtime_profile": runtime_profile} if runtime_profile else model, gpu_info, configured=False),
    }


def _downloaded_catalog_path(model: dict[str, Any], downloaded_files: dict[str, Path]) -> tuple[bool, Path | None, set[str]]:
    parts = model.get("gguf_parts") or []
    if parts:
        part_paths = [
            downloaded_files.get(str(part.get("file", "")).lower())
            for part in parts
            if isinstance(part, dict)
        ]
        downloaded = bool(part_paths) and all(part_paths)
        seen = {str(part.get("file", "")).lower() for part in parts if isinstance(part, dict)}
        return downloaded, part_paths[0] if downloaded else None, seen if downloaded else set()

    gguf = str(model["gguf"]).lower()
    path = downloaded_files.get(gguf)
    return bool(path), path, {gguf} if path else set()


def build_models_payload(gpu_info: Optional[GPUInfo], loaded_model: Optional[str], live_tps: float,
                         install_dir: str | Path, data_dir: str | Path | None = None,
                         context_length: Optional[int] = None,
                         catalog: list[dict[str, Any]] | None = None,
                         evidence: list[dict[str, Any]] | None = None,
                         downloaded_files_override: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog = [
        model
        for model in (normalize_catalog_entry(raw) for raw in (catalog or load_model_catalog(install_dir)))
        if model is not None
    ]
    evidence = load_evidence() if evidence is None else evidence
    recommendation = _recommendation_from_env(install_dir)
    configured_model = recommendation.get("model") or read_env_value("LLM_MODEL", install_dir)
    configured_gguf = recommendation.get("gguf") or read_env_value("GGUF_FILE", install_dir)
    configured_entry = find_catalog_model(catalog, configured_model, configured_gguf)
    profile = _model_profile(install_dir)
    try:
        install_ram_gb = int(read_env_file_value("SYSTEM_RAM_GB", install_dir) or read_env_value("SYSTEM_RAM_GB", install_dir) or 0)
    except ValueError:
        install_ram_gb = 0
    if gpu_info is None:
        gpu_info = _host_amd_runtime_gpu_from_env(install_dir, install_ram_gb)
    ranked_recommendations = rank_pre_download_models(catalog, gpu_info, profile=profile, limit=3, system_ram_gb=install_ram_gb or None)
    recommended_entry = configured_entry or (ranked_recommendations[0] if ranked_recommendations else None)
    flags = collect_runtime_flags(install_dir)
    runtime = read_env_value("LLM_BACKEND", install_dir) or os.environ.get("LLM_BACKEND") or "llama-server"
    runtime_profile_text = " ".join([
        read_env_value("MODEL_RUNTIME_PROFILE", install_dir),
        read_env_value("MODEL_RUNTIME_PROFILE_LABEL", install_dir),
        read_env_value("LLAMA_SERVER_IMAGE", install_dir),
    ])
    if "turboquant" in normalize_key(runtime_profile_text):
        runtime = "turboquant"
    data_root = Path(data_dir) if data_dir is not None else Path(install_dir) / "data"
    models_dir = model_files_dir(data_root)
    if downloaded_files_override is not None:
        downloaded_files = {
            str(name).lower(): value if isinstance(value, Path) else models_dir / str(name)
            for name, value in downloaded_files_override.items()
        }
    else:
        downloaded_files = {}
        if models_dir.exists():
            for p in models_dir.iterdir():
                try:
                    if p.is_file() and p.name.lower().endswith(".gguf") and p.stat().st_size > 0:
                        downloaded_files[p.name.lower()] = p
                except OSError:
                    continue

    gpu_data = None
    free_gb = 0.0
    if gpu_info:
        vram_total = round(gpu_info.memory_total_mb / 1024, 1)
        vram_used = round(gpu_info.memory_used_mb / 1024, 1)
        free_gb = max(vram_total - vram_used, 0.0)
        gpu_data = {
            "vramTotal": vram_total,
            "vramUsed": vram_used,
            "vramFree": round(free_gb, 1),
            "name": gpu_info.name,
            "backend": gpu_info.gpu_backend or "unknown",
        }

    response_models = []
    seen_files: set[str] = set()
    current_model_id = None
    configured_model_id = configured_entry["id"] if configured_entry else configured_model or configured_gguf or None

    def append_model(model: dict[str, Any], path: Path | None, status_if_not_loaded: str) -> None:
        nonlocal current_model_id
        is_loaded = bool(loaded_model and current_model_matches(model, loaded_model, loaded_model))
        is_configured = configured_entry is not None and model["id"] == configured_entry["id"]
        is_recommended = recommended_entry is not None and model["id"] == recommended_entry["id"]
        if is_loaded:
            current_model_id = model["id"]
        metadata = inspect_gguf(path) if path else {"exists": False, "readable": False, "quantization": model.get("quantization", "unknown")}
        runtime_profile = _matching_runtime_profile(model, gpu_info, install_ram_gb or None)
        profile_context = _effective_context_length(model, runtime_profile)
        configured_context = recommendation.get("contextLength") if is_configured else None
        actual_context = (
            context_length
            if is_loaded and context_length
            else configured_context
            or profile_context
            or model.get("context_length")
        )
        vram_required = float(model["vram_required_gb"])
        selector_required = _effective_required_memory_gb({**model, "context_length": actual_context}, runtime_profile)
        if gpu_info:
            capacity_gb = _usable_model_memory_gb(gpu_info)
            fits_total = bool(_fits_declared_vram(selector_required, capacity_gb) or is_loaded)
            fits_current = bool(_fits_declared_vram(selector_required, free_gb) or is_loaded)
        else:
            fits_total = bool(_fits_declared_vram(selector_required, 4.0) or is_loaded)
            fits_current = False
        perf = evaluate_performance(model, gpu_info, metadata, is_loaded, live_tps, actual_context, flags, evidence, fits_total, runtime)
        reason = recommendation.get("reason") if is_configured else ""
        if is_recommended and not is_loaded and perf["source"] == "benchmark_required":
            perf = {
                **perf,
                "label": recommendation["performanceLabel"],
                "hardwareMatch": {
                    **perf["hardwareMatch"],
                    "recommendationSource": recommendation["source"] if is_configured else "catalog_fit_pre_download",
                    "recommendationConfidence": recommendation["confidence"] if is_configured else "medium",
                },
            }
        quantization = metadata.get("quantization")
        if not quantization or quantization == "unknown":
            quantization = model.get("quantization")
        model_recommendation = None
        if is_recommended:
            model_recommendation = {
                **recommendation,
                "source": recommendation["source"] if is_configured else "catalog_fit_pre_download",
                "confidence": recommendation["confidence"] if is_configured else "medium",
                "reason": reason or _catalog_fit_reason({**model, "_runtime_profile": runtime_profile}, gpu_info, is_configured),
                "model": model.get("llm_model_name") or model["id"],
                "gguf": model.get("gguf"),
                "contextLength": actual_context,
            }
        response_models.append({
            "id": model["id"],
            "name": model["name"],
            "gguf": model.get("gguf"),
            "ggufParts": model.get("gguf_parts") or None,
            "downloadUrl": model.get("gguf_url") or None,
            "downloadSha256": model.get("gguf_sha256") or None,
            "llmModelName": model.get("llm_model_name") or None,
            "size": format_size(model["size_mb"]),
            "sizeGb": round(float(model["size_mb"]) / 1024, 1),
            "vramRequired": vram_required,
            "estimatedRequired": selector_required,
            "contextLength": actual_context,
            "specialty": model["specialty"],
            "description": model["description"],
            "tokensPerSecEstimate": model.get("tokens_per_sec_estimate"),
            "tokensPerSec": perf["tokensPerSec"],
            "quantization": quantization,
            "architecture": metadata.get("architecture") if metadata.get("architecture") != "unknown" else model.get("architecture", "dense"),
            "activeParamsB": model.get("active_params_b"),
            "publisher": model_publisher(model),
            "metadata": {
                "source": "gguf" if metadata.get("readable") else "catalog",
                "catalogSource": model.get("catalog_source") or "ods",
                "sourceRepo": model.get("source_repo"),
                "sourceRevision": model.get("source_revision"),
                "sourceUrl": model.get("source_url"),
                "license": model.get("license"),
                "importedAt": model.get("imported_at"),
                "contextSource": model.get("context_source"),
                "readable": bool(metadata.get("readable")),
                "blockCount": metadata.get("block_count"),
                "expertCount": metadata.get("expert_count"),
                "expertUsedCount": metadata.get("expert_used_count"),
            },
            "appCompatibility": model_app_compatibility(
                model,
                perf,
                model_compatibility_runtime_context(install_dir, gpu_info, runtime),
            ),
            "status": "loaded" if is_loaded else status_if_not_loaded,
            "recommended": is_recommended,
            "configured": is_configured,
            "recommendation": model_recommendation,
            "fitsVram": fits_total,
            "fitsCurrentVram": fits_current,
            "fitLabel": runtime_profile.get("fit_label") if runtime_profile else ("Fits GPU" if fits_total else "Too large"),
            "runtimeProfile": {
                "id": runtime_profile.get("id"),
                "label": runtime_profile.get("label"),
                "runtime": runtime_profile.get("runtime"),
                "sourceUrl": runtime_profile.get("source_url"),
            } if runtime_profile else None,
            "performance": perf,
            "performanceLabel": perf["label"],
        })

    for model in catalog:
        downloaded, path, seen = _downloaded_catalog_path(model, downloaded_files)
        seen_files.update(seen)
        append_model(model, path, "downloaded" if downloaded else "available")

    for path in downloaded_files.values():
        if path.name.lower() in seen_files:
            continue
        if not path.exists():
            continue
        size_mb = path.stat().st_size / (1024 * 1024)
        fallback = {
            "id": local_model_id(path.stem),
            "name": path.stem,
            "gguf": path.name,
            "size_mb": size_mb,
            "vram_required_gb": round((size_mb / 1024) + 1.5, 1),
            "context_length": int(read_env_value("MAX_CONTEXT", install_dir) or read_env_value("CTX_SIZE", install_dir) or 32768),
            "specialty": "Local",
            "description": "Locally installed GGUF model.",
            "quantization": "GGUF",
            "decode_read_mb": size_mb,
        }
        append_model(fallback, path, "downloaded")

    return {
        "models": response_models,
        "gpu": gpu_data,
        "currentModel": current_model_id,
        "loadedModel": loaded_model,
        "configuredModel": configured_model_id,
        "hermesMinimumContext": HERMES_MIN_CONTEXT,
        "hermesTargetContext": HERMES_TARGET_CONTEXT,
        "recommendationPolicy": recommendation.get("selectionPolicy") or _DEFAULT_RECOMMENDATION_POLICY,
        "recommendationAlternatives": [
            _recommendation_alternative(model, gpu_info)
            for model in ranked_recommendations
        ],
    }
