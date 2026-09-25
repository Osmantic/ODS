"""Evidence-first performance metadata for the dashboard model library.

The oracle is deliberately conservative:
1. measured local throughput from this machine;
2. exact published benchmark match;
3. calibrated prediction from another local measurement on this machine;
4. benchmark required.

It never returns catalog tok/s estimates as observed performance.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from env_values import parse_env_value, strip_matching_quotes
from gguf_inspector import inspect_gguf
from model_mtp import mtp_metadata
from context_policy import HERMES_MIN_CONTEXT, HERMES_TARGET_CONTEXT, PIXEL_MIN_CONTEXT
from helpers import (
    get_model_performance_samples,
    get_recorded_model_performance,
    is_plausible_single_request_tps,
)
from model_memory import memory_metadata, required_model_memory_gb
from model_selection import (
    POLICY as _SHARED_SELECTOR_POLICY,
    family_allowed as _shared_family_allowed,
    hardware_matching_profiles as _shared_hardware_matching_profiles,
    matching_runtime_profile as _shared_matching_runtime_profile,
    memory_class as _shared_memory_class,
    plan_model_context,
    rank_catalog_models,
    value_enabled as _value_enabled,
)
from models import GPUInfo


_EVIDENCE_PATH = Path(__file__).with_name("performance_evidence.json")
_DEFAULT_RECOMMENDATION_POLICY = "catalog-fit-pre-download"
_VRAM_FIT_TOLERANCE_GB = 0.25
_MODEL_SELECTOR_POLICY = _SHARED_SELECTOR_POLICY
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
        return strip_matching_quotes(value)
    return read_env_file_value(key, install_dir)


def read_env_file_value(key: str, install_dir: str | Path) -> str:
    env_path = Path(install_dir) / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return parse_env_value(line.split("=", 1)[1])
    except OSError:
        pass
    return ""


def read_persisted_env_value(key: str, install_dir: str | Path) -> str:
    """Read mutable install config from .env before the container environment."""
    env_path = Path(install_dir) / ".env"
    file_value = read_env_file_value(key, install_dir)
    if file_value or env_path.exists():
        return file_value
    return strip_matching_quotes(os.environ.get(key, ""))


def read_context_length(install_dir: str | Path, default: int = 32768) -> int:
    for reader in (read_env_file_value, read_env_value):
        for key in ("CTX_SIZE", "MAX_CONTEXT"):
            try:
                value = int(reader(key, install_dir) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return default


def model_files_dir(data_dir: str | Path) -> Path:
    return Path(data_dir) / "models"


def installed_model_path(data_dir: str | Path, filename: str) -> Path | None:
    from model_stores import resolve_model_file
    return resolve_model_file(Path(data_dir), filename, container=Path("/.dockerenv").exists())


def _model_aliases(model: dict[str, Any]) -> set[str]:
    aliases = {
        str(model.get("id") or ""),
        str(model.get("name") or ""),
        str(model.get("gguf") or ""),
        str(model.get("gguf_file") or ""),
        str(model.get("llm_model_name") or ""),
    }
    aliases.update(str(alias or "") for alias in model.get("aliases", []) or [])
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
    context_limit_known = raw.get("context_limit_known") is not False
    # Whether the entry itself states its native maximum (a catalog
    # max_context_length, or an import's GGUF header value). When it does
    # not, max_context_length below falls back to context_length for the
    # context options, and model_selection.declared_max_context must not
    # treat that fallback as a native ceiling.
    native_context_declared = context_limit_known and bool(
        raw.get("max_context_length") or raw.get("maxContextLength")
    )
    if context_limit_known:
        try:
            max_context_length = int(
                raw.get("max_context_length")
                or raw.get("maxContextLength")
                or context_length
            )
        except (TypeError, ValueError):
            max_context_length = context_length
            native_context_declared = False
    else:
        max_context_length = 0

    aliases = set(_model_aliases(raw))
    if raw.get("llm_model_name"):
        aliases.add(str(raw["llm_model_name"]))

    model = {
        **memory_metadata(raw),
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
        "max_context_length": max(max_context_length, context_length) if context_limit_known else 0,
        "context_limit_known": context_limit_known,
        "native_context_declared": native_context_declared,
        "specialty": raw.get("specialty", "General"),
        "description": raw.get("description", ""),
        "quantization": raw.get("quantization"),
        "architecture": raw.get("architecture", "dense"),
        "active_params_b": raw.get("active_params_b"),
        "tokens_per_sec_estimate": raw.get("tokens_per_sec_estimate"),
        "runtime_profiles": raw.get("runtime_profiles") if isinstance(raw.get("runtime_profiles"), list) else [],
        "install_recommendation": _value_enabled(raw.get("install_recommendation", True)),
        "selection": raw.get("selection") if isinstance(raw.get("selection"), dict) else {},
        "mtp": raw.get("mtp") if isinstance(raw.get("mtp"), dict) else {},
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


def _fresh_compatibility_override(raw: dict[str, Any]) -> bool:
    """Do not promote an undated, malformed, or expired host-specific claim."""
    expires_at = raw.get("expiresAt")
    if not isinstance(expires_at, str):
        return False
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return expiry.tzinfo is not None and expiry > datetime.now(timezone.utc)


# User-facing compatibility copy.
#
# Catalog ``app_compatibility.<app>.reason`` and ``evidence`` are internal
# fleet-QA notes (run IDs, host names, harness vocabulary). They stay in the
# catalog and in API payloads for operators and tooling, but no UI may render
# them. UIs render ``userMessage`` instead: an optional per-entry catalog
# ``userNote`` when one is written, otherwise generic copy keyed by app and
# status. Status values and their semantics are unchanged.
COMPATIBILITY_BLOCKING_STATUSES = frozenset({
    "blocked",
    "incompatible",
    "not_agent_viable",
    "not_recommended",
    "not_supported",
    "unsupported",
    "unsupported_until_revalidated",
})
_COMPATIBILITY_VERIFIED_STATUSES = frozenset({
    "agent_viable",
    "pixel_agent_viable",
    "supported",
    "verified",
})
_USER_NOTE_MAX_CHARS = 280
_COMPATIBILITY_USER_COPY = {
    "hermesTalk": {
        "blocked": "This model isn't supported in ODS Talk yet. Switch to a recommended model to use ODS Talk.",
        "verified": "Verified with ODS Talk.",
        "unknown": "Not yet tested with ODS Talk.",
    },
    "agentViability": {
        "blocked": "Not verified for agent tasks, so responses may fail. Switch to a recommended model for agent features.",
        "verified": "Verified for agent tasks.",
        "unknown": "Not yet tested for agent tasks.",
    },
    "pixelAgent": {
        "blocked": "Not verified for Portal agent tasks, so tool use may be unreliable.",
        "verified": "Verified for Portal agent tasks.",
        "unknown": "Not yet tested for Portal agent tasks.",
    },
    "openaiChat": {
        "blocked": "This model isn't supported for chat yet. Switch to a recommended model to chat.",
        "verified": "Verified for chat.",
        "unknown": "Not yet tested for chat.",
    },
}
_COMPATIBILITY_APP_NAMES = {
    "litellm": "LiteLLM",
    "openWebui": "Open WebUI",
    "openclaw": "OpenClaw",
    "opencode": "OpenCode",
    "perplexica": "Perplexica",
}


def _compatibility_status_group(status: Any) -> str:
    normalized = normalize_key(status).replace("-", "_")
    if normalized in COMPATIBILITY_BLOCKING_STATUSES:
        return "blocked"
    if normalized in _COMPATIBILITY_VERIFIED_STATUSES:
        return "verified"
    return "unknown"


def _compatibility_app_name(app_key: str) -> str:
    if app_key in _COMPATIBILITY_APP_NAMES:
        return _COMPATIBILITY_APP_NAMES[app_key]
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(app_key or "")).split()
    return " ".join(word[:1].upper() + word[1:] for word in words) or "this app"


def compatibility_user_message(app_key: str, status: Any, user_note: Any = None) -> str:
    """Return end-user copy for one app compatibility verdict.

    ``app_key`` is the camelCase payload key (``hermesTalk``, ``perplexica``).
    A catalog ``userNote`` wins when present; the internal ``reason`` is never
    used here.
    """
    if isinstance(user_note, str):
        note = " ".join(user_note.split())
        if note and len(note) <= _USER_NOTE_MAX_CHARS:
            return note
    group = _compatibility_status_group(status)
    copy = _COMPATIBILITY_USER_COPY.get(app_key)
    if copy:
        return copy[group]
    name = _compatibility_app_name(app_key)
    if group == "blocked":
        return f"This model isn't supported in {name} yet. Switch to a recommended model to use {name}."
    if group == "verified":
        return f"Verified with {name}."
    return f"Not yet tested with {name}."


def _app_compatibility_entry(
    raw: Any,
    default_label: str,
    runtime_context: Optional[dict[str, Any]] = None,
    app_key: str = "",
) -> dict[str, Any]:
    if isinstance(raw, dict):
        # A fresh positive result on one host must not erase an older negative
        # result on other hosts. Only an explicitly host-scoped override can
        # replace the default record for a matching runtime context.
        overrides = raw.get("scopedOverrides")
        if isinstance(overrides, list):
            for override in overrides:
                if (
                    isinstance(override, dict)
                    and _scope_values(
                        override, "hostScope", "host_scope", "fleetHostScope", "fleet_host_scope"
                    )
                    and str(override.get("status") or "").strip()
                    and _fresh_compatibility_override(override)
                    and _compatibility_scope_matches(override, runtime_context)
                ):
                    raw = override
                    break
    if isinstance(raw, dict) and not _compatibility_scope_matches(raw, runtime_context):
        return {
            "status": "unknown",
            "label": default_label,
            "reason": "",
            "userMessage": compatibility_user_message(app_key, "unknown"),
        }

    user_note = None
    if isinstance(raw, dict):
        status = str(raw.get("status") or "unknown").strip() or "unknown"
        label = str(raw.get("label") or default_label).strip() or default_label
        reason = str(raw.get("reason") or "").strip()
        evidence = str(raw.get("evidence") or "").strip()
        user_note = raw.get("userNote", raw.get("user_note"))
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

    normalized_status = normalize_key(status).replace("-", "_") or "unknown"
    payload = {
        "status": normalized_status,
        "label": label,
        # Internal fleet-QA note. Kept for operators and tooling; never render.
        "reason": reason,
        "userMessage": compatibility_user_message(app_key, normalized_status, user_note),
    }
    if evidence:
        payload["evidence"] = evidence
    return payload


def _app_compatibility_payload_key(key: Any) -> str:
    raw = normalize_key(str(key or ""))
    if not raw:
        return ""
    aliases = {
        "agent-viability": "agentViability",
        "hermes-talk": "hermesTalk",
        "openai-chat": "openaiChat",
        "pixel-agent": "pixelAgent",
    }
    if raw in aliases:
        return aliases[raw]
    parts = [part for part in raw.split("-") if part]
    if not parts:
        return ""
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _app_compatibility_default_label(key: Any) -> str:
    parts = [part for part in normalize_key(str(key or "")).split("-") if part]
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
        "userSpeed": (
            f"{tokens_per_sec:.1f} tokens/sec measured, "
            f"{_AGENT_MIN_LOCAL_TOKENS_PER_SEC:.0f}+ needed"
        ),
        "reason": (
            f"Local measured throughput is {tokens_per_sec:.1f} tok/s, below the "
            f"{_AGENT_MIN_LOCAL_TOKENS_PER_SEC:.1f} tok/s floor for ODS Talk and "
            "agent-required workflows on this machine."
        ),
    }


_TALK_BLOCKING_STATUSES = frozenset({
    "blocked",
    "incompatible",
    "not_agent_viable",
    "not_recommended",
    "not_supported",
    "unsupported",
    "unsupported_until_revalidated",
})


def _context_k(value: int) -> str:
    return f"{value / 1024:g}K"


def hermes_context_block(
    model: dict[str, Any],
    context_length: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """ODS Talk verdict when the context rules Hermes out, else None.

    Hermes refuses a model below :data:`HERMES_MIN_CONTEXT` with an HTTP 502
    after the model is already loaded. Say so up front instead: either the
    model's own maximum is below the floor, or ``context_length`` (the
    context it is served at, or will be served at on this hardware) is.
    """
    try:
        native = int(model.get("max_context_length") or 0)
    except (TypeError, ValueError):
        native = 0
    limit_known = model.get("context_limit_known") is not False
    if limit_known and 0 < native < HERMES_MIN_CONTEXT:
        return {
            "status": "unsupported",
            "label": "Context too small for ODS Talk",
            "reason": f"model maximum context {native} is below the Hermes minimum {HERMES_MIN_CONTEXT}",
            "userMessage": (
                f"ODS Talk needs at least {_context_k(HERMES_MIN_CONTEXT)} of context; "
                f"this model supports only {_context_k(native)}."
            ),
            "code": "context_below_hermes_minimum",
        }
    try:
        served = int(context_length or 0)
    except (TypeError, ValueError):
        served = 0
    if 0 < served < HERMES_MIN_CONTEXT:
        return {
            "status": "unsupported",
            "label": "Context too small for ODS Talk",
            "reason": f"served context {served} is below the Hermes minimum {HERMES_MIN_CONTEXT}",
            "userMessage": (
                f"ODS Talk needs at least {_context_k(HERMES_MIN_CONTEXT)} of context; this model "
                f"runs at {_context_k(served)} here. Load it with a {_context_k(HERMES_MIN_CONTEXT)} "
                "context in Models, or choose a model that fits at that size."
            ),
            "code": "context_below_hermes_minimum",
        }
    return None


def model_app_compatibility(
    model: dict[str, Any],
    performance: Optional[dict[str, Any]] = None,
    runtime_context: Optional[dict[str, Any]] = None,
    context_length: Optional[int] = None,
) -> dict[str, Any]:
    """App verdicts for ``model``.

    ``context_length`` is the context the model is (or will be) served at;
    below the Hermes floor it rules ODS Talk out on its own.
    """
    raw = model.get("app_compatibility") if isinstance(model.get("app_compatibility"), dict) else {}
    hermes_talk = _app_compatibility_entry(
        raw.get("hermes_talk"), "ODS Talk untested", runtime_context, "hermesTalk"
    )
    context_block = hermes_context_block(model, context_length)
    if context_block and hermes_talk.get("status") not in _TALK_BLOCKING_STATUSES:
        hermes_talk = context_block
    compatibility = {
        "openaiChat": _app_compatibility_entry(
            raw.get("openai_chat"), "Direct chat untested", runtime_context, "openaiChat"
        ),
        "hermesTalk": hermes_talk,
        "agentViability": _agent_viability_entry(raw.get("agent_viability"), hermes_talk, runtime_context),
        "pixelAgent": _app_compatibility_entry(
            raw.get("pixel_agent"), "Portal agent untested", runtime_context, "pixelAgent"
        ),
    }
    for raw_key, raw_value in raw.items():
        payload_key = _app_compatibility_payload_key(raw_key)
        if not payload_key or payload_key in compatibility:
            continue
        compatibility[payload_key] = _app_compatibility_entry(
            raw_value,
            _app_compatibility_default_label(raw_key),
            runtime_context,
            payload_key,
        )
    exact_speed_block = _exact_performance_agent_block(performance)
    if exact_speed_block:
        speed = exact_speed_block["userSpeed"]
        compatibility["hermesTalk"] = {
            "status": "unsupported_until_revalidated",
            "label": "Too slow for ODS Talk",
            "reason": exact_speed_block["reason"],
            "userMessage": (
                f"This model is too slow on this machine for ODS Talk ({speed}). "
                "Switch to a smaller or faster model to use ODS Talk."
            ),
        }
        compatibility["agentViability"] = {
            "status": "not_agent_viable",
            "label": "Too slow for agents",
            "reason": exact_speed_block["reason"],
            "userMessage": f"Too slow on this machine for agent tasks ({speed}).",
        }
        compatibility["pixelAgent"] = {
            "status": "not_agent_viable",
            "label": "Too slow for Portal",
            "reason": exact_speed_block["reason"],
            "userMessage": f"Too slow on this machine for Portal agent tasks ({speed}).",
        }
    return compatibility


def _agent_viability_entry(
    raw: Any,
    hermes_talk: dict[str, Any],
    runtime_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if raw:
        return _app_compatibility_entry(raw, "Agent viability untested", runtime_context, "agentViability")

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
            "userMessage": compatibility_user_message("agentViability", "not_agent_viable"),
        }
        if hermes_evidence:
            payload["evidence"] = hermes_evidence
        return payload
    if hermes_status in {"supported", "verified"}:
        payload = {
            "status": "agent_viable",
            "label": "Agent viable",
            "reason": hermes_reason,
            "userMessage": compatibility_user_message("agentViability", "agent_viable"),
        }
        if hermes_evidence:
            payload["evidence"] = hermes_evidence
        return payload
    return {
        "status": "unknown",
        "label": "Agent viability untested",
        "reason": "",
        "userMessage": compatibility_user_message("agentViability", "unknown"),
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
        "LLAMA_ARG_CHECKPOINT_EVERY_NT": "checkpoint_every_n_tokens",
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


def _selector_required_memory_gb(model: dict[str, Any]) -> float:
    return required_model_memory_gb(model)


def _gpu_memory_type(gpu_info: Optional[GPUInfo]) -> str:
    if not gpu_info:
        return "discrete"
    backend = normalize_key(gpu_info.gpu_backend)
    return (
        "unified"
        if backend == "apple"
        or normalize_key(getattr(gpu_info, "memory_type", "")) == "unified"
        or "strix-halo" in normalize_key(gpu_info.name)
        else "discrete"
    )


def _gpu_memory_class(gpu_info: Optional[GPUInfo]) -> str:
    if not gpu_info:
        return "cpu"
    return _shared_memory_class(gpu_info.gpu_backend, _gpu_memory_type(gpu_info), gpu_info.memory_total_mb)


def _hardware_matching_runtime_profiles(model: dict[str, Any], gpu_info: Optional[GPUInfo],
                                       system_ram_gb: int | None = None) -> list[dict[str, Any]]:
    """Return profiles anchored to the detected GPU before system-RAM filtering."""
    if not gpu_info:
        return []
    ram_gb = system_ram_gb if system_ram_gb is not None else _system_ram_gb()
    return _shared_hardware_matching_profiles(
        model, gpu_info.gpu_backend, _gpu_memory_type(gpu_info),
        gpu_info.memory_total_mb or 0, platform.machine(), ram_gb,
    )


def _matching_runtime_profile(model: dict[str, Any], gpu_info: Optional[GPUInfo],
                              system_ram_gb: int | None = None) -> dict[str, Any] | None:
    if not gpu_info:
        return None
    ram_gb = system_ram_gb if system_ram_gb is not None else _system_ram_gb()
    return _shared_matching_runtime_profile(
        model, gpu_info.gpu_backend, _gpu_memory_type(gpu_info),
        gpu_info.memory_total_mb or 0, ram_gb, platform.machine(),
    )


def planned_model_context(
    model: dict[str, Any],
    gpu_info: Optional[GPUInfo],
    system_ram_gb: int | None = None,
    *,
    preferred_context: int | None = None,
    min_context: int = HERMES_MIN_CONTEXT,
) -> dict[str, Any]:
    """The context ``model`` is served at on this machine (install policy).

    The dashboard model list, a model switch (POST /api/models/{id}/load)
    and a restore of the installer's pick all use this, and it is the same
    code the installer's selector runs (model_selection.plan_model_context):
    the Hermes floor when it fits, otherwise the largest context that does.
    """
    if gpu_info:
        ram_gb = system_ram_gb if system_ram_gb is not None else _system_ram_gb()
        return plan_model_context(
            model,
            capacity_gb=_usable_model_memory_gb(gpu_info, ram_gb),
            backend=gpu_info.gpu_backend,
            memory_type=_gpu_memory_type(gpu_info),
            vram_mb=gpu_info.memory_total_mb or 0,
            ram_gb=ram_gb,
            host_arch=platform.machine(),
            min_context=min_context,
            preferred_context=preferred_context,
        )
    # Without hardware information no runtime profile applies and the
    # historical 4 GB ceiling bounds the plan (see rank_pre_download_models).
    return plan_model_context(
        model,
        capacity_gb=4.0,
        backend="undetected",
        memory_type="discrete",
        vram_mb=0,
        ram_gb=system_ram_gb or 0,
        host_arch=platform.machine(),
        min_context=min_context,
        preferred_context=preferred_context,
    )


def activation_context_plan(
    model: dict[str, Any],
    install_dir: str | Path,
    gpu_info: Optional[GPUInfo] = None,
    *,
    preferred_context: int | None = None,
) -> Optional[dict[str, Any]]:
    """:func:`planned_model_context` for a switch on this install.

    Uses the installer-recorded SYSTEM_RAM_GB and, on Windows AMD native
    runtimes the container cannot inspect, the same GPU surrogate the model
    list uses. Returns None when the hardware is unknown, so the caller keeps
    the host agent's default rather than planning for a guessed 4 GB.
    """
    try:
        ram_gb = int(read_env_file_value("SYSTEM_RAM_GB", install_dir) or read_env_value("SYSTEM_RAM_GB", install_dir) or 0)
    except ValueError:
        ram_gb = 0
    if gpu_info is None:
        gpu_info = _host_amd_runtime_gpu_from_env(install_dir, ram_gb)
    if gpu_info is None:
        return None
    return planned_model_context(
        model, gpu_info, ram_gb or None, preferred_context=preferred_context,
    )


def _effective_context_length(model: dict[str, Any], runtime_profile: dict[str, Any] | None = None) -> int:
    if runtime_profile and runtime_profile.get("context_length"):
        return int(runtime_profile["context_length"])
    return int(model.get("context_length") or 0)


def _effective_required_memory_gb(model: dict[str, Any],
                                  runtime_profile: dict[str, Any] | None = None) -> float:
    context_length = None
    if runtime_profile and runtime_profile.get("context_length"):
        context_length = int(runtime_profile["context_length"])
    return required_model_memory_gb(
        model,
        context_length=context_length,
        runtime_profile=runtime_profile,
    )


def _context_memory_required_gb(
    model: dict[str, Any],
    runtime_profile: dict[str, Any] | None,
    context_length: int,
) -> float:
    """Estimate memory while retaining a matched profile's calibrated baseline."""
    requested = {**model, "context_length": int(context_length)}
    raw_requested = _selector_required_memory_gb(requested)
    if not runtime_profile or runtime_profile.get("estimated_required_gb") is None:
        return raw_requested

    try:
        profile_context = int(runtime_profile.get("context_length") or 0)
        profile_required = float(runtime_profile["estimated_required_gb"])
    except (TypeError, ValueError):
        return raw_requested
    if profile_context <= 0:
        return raw_requested

    raw_profile = _selector_required_memory_gb(
        {**model, "context_length": profile_context}
    )
    return round(max(float(model.get("vram_required_gb") or 0), profile_required + raw_requested - raw_profile), 2)


def _context_options(
    model: dict[str, Any],
    runtime_profile: dict[str, Any] | None,
    gpu_info: Optional[GPUInfo],
) -> list[dict[str, Any]]:
    context_limit_known = model.get("context_limit_known") is not False
    try:
        maximum = max(
            int(
                model.get("max_context_length")
                or model.get("context_length")
                or 0
            ),
            1024,
        )
    except (TypeError, ValueError):
        maximum = 32768
    recommended = max(min(_effective_context_length(model, runtime_profile) or maximum, maximum), 1024)
    values = {
        value
        for value in (8192, 16384, 32768, 65536, 131072, 262144)
        if value <= maximum
    }
    values.update({recommended, maximum})
    capacity = _usable_model_memory_gb(gpu_info) if gpu_info else 0.0
    return [
        {
            "contextLength": value,
            "estimatedRequired": _context_memory_required_gb(
                model,
                runtime_profile,
                value,
            ),
            "recommended": value == recommended,
            "fullContext": context_limit_known and value == maximum,
            "fitsVram": (
                _fits_declared_vram(
                    _context_memory_required_gb(model, runtime_profile, value),
                    capacity,
                )
                if gpu_info
                else None
            ),
        }
        for value in sorted(values)
    ]


def _usable_model_memory_gb(gpu_info: Optional[GPUInfo], system_ram_gb: int | None = None) -> float:
    if not gpu_info:
        return 0.0
    total_gb = gpu_info.memory_total_mb / 1024
    backend = normalize_key(gpu_info.gpu_backend)
    if backend == "apple" or normalize_key(gpu_info.memory_type) == "unified" or "strix-halo" in normalize_key(gpu_info.name):
        ram_gb = system_ram_gb if system_ram_gb is not None else total_gb
        return max(ram_gb * 0.55, 2.0)
    if backend in {"cpu", "none", "unknown"} or total_gb <= 0:
        ram_gb = system_ram_gb if system_ram_gb is not None else _system_ram_gb()
        return min(max(ram_gb * 0.35, 3.0), 8.0)
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
    # The default profile is the broad open-model lane. It keeps Gemma out so a
    # user who explicitly wants Gemma does not get a Qwen-family recommendation
    # and vice versa; the Gemma profile keeps the tiny Qwen bootstrap fallback.
    return _shared_family_allowed(model, profile)


def _installable(model: dict[str, Any]) -> bool:
    return bool(model.get("gguf_url")) and _value_enabled(model.get("install_recommendation", True))


def rank_pre_download_models(catalog: list[dict[str, Any]], gpu_info: Optional[GPUInfo],
                             profile: str = "qwen", installable_only: bool = False,
                             limit: int = 3, system_ram_gb: int | None = None) -> list[dict[str, Any]]:
    """Rank catalog entries before any model is installed.

    Uses the installer's ranking (model_selection.rank_catalog_models) with
    the Hermes 64K floor as a soft minimum, so the dashboard recommends what
    scripts/select-model.py installs on the same hardware. It intentionally
    does not turn catalog tok/s estimates into displayed performance.
    """
    if not catalog:
        return []

    normalized_profile = _model_profile(explicit_profile=profile)
    if normalized_profile == "auto":
        normalized_profile = "qwen"
    capacity_gb = _usable_model_memory_gb(gpu_info, system_ram_gb) if gpu_info else 4.0
    ram_gb = system_ram_gb if system_ram_gb is not None else (_system_ram_gb() if gpu_info else 0)
    ranked = rank_catalog_models(
        catalog,
        capacity_gb=capacity_gb,
        profile=normalized_profile,
        installable_only=installable_only,
        # Without hardware information no runtime profile applies (not even
        # a CPU one); "undetected" matches no profile backend.
        backend=gpu_info.gpu_backend if gpu_info else "undetected",
        memory_type=_gpu_memory_type(gpu_info),
        vram_mb=(gpu_info.memory_total_mb or 0) if gpu_info else 0,
        ram_gb=ram_gb,
        host_arch=platform.machine(),
        min_context=HERMES_MIN_CONTEXT,
        installable=_installable,
    )
    return [candidate.as_model() for candidate in ranked[:max(limit, 1)]]


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
        # A shard belongs to this catalog entry even before the whole download
        # finishes; it must never become a standalone installed-model option.
        return downloaded, part_paths[0] if downloaded else None, seen

    gguf = str(model["gguf"]).lower()
    path = downloaded_files.get(gguf)
    return bool(path), path, {gguf} if path else set()


def _measured_native_activation(model, path, data_root, install_dir, gpu_info, system_ram_gb):
    """Separate a proven native load from the generic all-GPU fit estimate."""
    if path is None or not gpu_info:
        return None
    value = lambda key: read_env_file_value(key, install_dir) or read_env_value(key, install_dir)
    if (value("AMD_INFERENCE_LOCATION") != "host"
            or not value("AMD_INFERENCE_RUNTIME_MODE").startswith("windows-")
            or value("LLM_BACKEND") != "lemonade"
            or value("AMD_INFERENCE_MANAGED").lower() == "false"
            or value("LEMONADE_EXTERNAL").lower() == "true"):
        return None
    from model_stores import registered_stores, safe_artifact
    for store in registered_stores(data_root, container=Path("/.dockerenv").exists()):
        if store["path"].resolve() != path.parent.resolve():
            continue
        profiles = store.get("profiles", {})
        row = profiles.get(path.name) if isinstance(profiles, dict) else None
        fit = row.get("memoryQualification") if isinstance(row, dict) else None
        if not isinstance(fit, dict) or fit.get("source") != "measured-native" or fit.get("schemaVersion") != 1:
            continue
        if (fit.get("runtimeMode") != "lemonade" or fit.get("gpuLayers") != "99"
                or fit.get("runtimeBackend") != row.get("backend")
                or fit.get("qualificationSignature") != row.get("qualificationSignature")
                or any(not re.fullmatch(r"[0-9a-f]{64}", str(fit.get(key, ""))) for key in ("modelSha256", "runtimeSha256", "executionSignature", "visionProjectorSha256"))
                or any(fit.get(key) != row.get(key) for key in ("modelSha256", "runtimeSha256", "contextLength", "draftTokens"))
                or (model.get("gguf_sha256") and model["gguf_sha256"] != fit["modelSha256"])):
            continue
        hardware = fit.get("hardware", {})
        if (not isinstance(hardware, dict) or normalize_key(hardware.get("name")) != normalize_key(gpu_info.name)
                or hardware.get("backend") != gpu_info.gpu_backend
                or type(hardware.get("memoryTotalMB")) is not int
                or gpu_info.memory_total_mb + 256 < hardware["memoryTotalMB"]
                or type(hardware.get("systemRamGB")) is not int or system_ram_gb < hardware["systemRamGB"]):
            continue
        projector = safe_artifact(store["path"], fit.get("visionProjectorFile"))
        try:
            if (projector is None or projector.stat().st_size != fit.get("visionProjectorSize")
                    or abs(projector.stat().st_mtime_ns - int(fit.get("visionProjectorMtimeNs", 0))) > 1_000_000_000):
                continue
        except (OSError, TypeError, ValueError):
            continue
        return {"available":True, "mode":"native-profile", "source":"measured-native",
            "contextLength":fit["contextLength"], "label":"Verified native profile",
            "measuredAt":fit.get("measuredAt")}
    return None


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
        from model_stores import scan_model_files
        downloaded_files = {name.lower(): path for name, path in scan_model_files(
            data_root, container=Path("/.dockerenv").exists()).items()}

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
        activation_support = _measured_native_activation(model, path, data_root, install_dir, gpu_info, install_ram_gb) if metadata.get("readable") else None
        runtime_profile = _matching_runtime_profile(model, gpu_info, install_ram_gb or None)
        profile_ram_ineligible = bool(
            runtime_profile is None
            and _hardware_matching_runtime_profiles(model, gpu_info, install_ram_gb or None)
        )
        # One policy for the listed context, a switch and a restore of the
        # installer's pick: the Hermes floor when it fits (see
        # planned_model_context). The installer's persisted context is only
        # the starting point, so a pick recorded below the floor is not
        # replayed below it when the floor fits.
        context_plan = planned_model_context(
            model, gpu_info, install_ram_gb or None,
            preferred_context=recommendation.get("contextLength") if is_configured else None,
        )
        if (
            not runtime_profile
            and not profile_ram_ineligible
            and context_plan["fits"]
            and context_plan["context_length"] != int(model.get("context_length") or 0)
        ):
            model = {
                **model,
                "max_context_length": model.get("max_context_length") or model.get("context_length"),
                "context_length": context_plan["context_length"],
            }
        profile_context = _effective_context_length(model, runtime_profile)
        configured_context = None
        if is_configured:
            configured_context = (
                context_plan["context_length"]
                if context_plan["fits"]
                else recommendation.get("contextLength")
            )
        actual_context = (
            context_length
            if is_loaded and context_length
            else configured_context
            or (activation_support["contextLength"] if activation_support else None)
            or profile_context
            or model.get("context_length")
        )
        file_context = int(metadata.get("context_length") or 0)
        context_limit_known = bool(file_context) or model.get("context_limit_known") is not False
        max_context_length = (
            file_context
            or (
                int(model.get("max_context_length") or model.get("context_length") or 0)
                if context_limit_known
                else 0
            )
        )
        memory_model = {**model, **metadata}
        context_model = {
            **memory_model,
            "context_length": actual_context,
            "max_context_length": max_context_length,
            "context_limit_known": context_limit_known,
        }
        vram_required = float(model["vram_required_gb"])
        selector_required = _effective_required_memory_gb(
            {**memory_model, "context_length": actual_context}, runtime_profile
        )
        if gpu_info:
            capacity_gb = _usable_model_memory_gb(gpu_info)
            fits_total = bool((not profile_ram_ineligible and _fits_declared_vram(selector_required, capacity_gb)) or is_loaded)
            fits_current = bool((not profile_ram_ineligible and _fits_declared_vram(selector_required, free_gb)) or is_loaded)
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
            "maxContextLength": max_context_length or None,
            "contextOptions": _context_options(context_model, runtime_profile, gpu_info),
            "specialty": model["specialty"],
            "description": model["description"],
            "tokensPerSecEstimate": model.get("tokens_per_sec_estimate"),
            "tokensPerSec": perf["tokensPerSec"],
            "quantization": quantization,
            "architecture": metadata.get("architecture") if metadata.get("architecture") != "unknown" else model.get("architecture", "dense"),
            "activeParamsB": model.get("active_params_b"),
            "publisher": model_publisher(model),
            "metadata": {
                "mtp": mtp_metadata(model, metadata),
                "source": "gguf" if metadata.get("readable") else "catalog",
                "catalogSource": model.get("catalog_source") or "ods",
                "sourceRepo": model.get("source_repo"),
                "sourceRevision": model.get("source_revision"),
                "sourceUrl": model.get("source_url"),
                "license": model.get("license"),
                "importedAt": model.get("imported_at"),
                "contextSource": "gguf_file" if file_context else model.get("context_source"),
                "contextLimitKnown": context_limit_known,
                "readable": bool(metadata.get("readable")),
                "blockCount": metadata.get("block_count"),
                "expertCount": metadata.get("expert_count"),
                "expertUsedCount": metadata.get("expert_used_count"),
            },
            "appCompatibility": model_app_compatibility(
                {**model, "max_context_length": max_context_length, "context_limit_known": context_limit_known},
                perf,
                model_compatibility_runtime_context(install_dir, gpu_info, runtime),
                context_length=actual_context,
            ),
            "status": "loaded" if is_loaded else status_if_not_loaded,
            "recommended": is_recommended,
            "configured": is_configured,
            "recommendation": model_recommendation,
            "fitsVram": fits_total,
            "activationSupport": activation_support,
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
            "context_length": read_context_length(install_dir),
            "specialty": "Local",
            "description": "Locally installed GGUF model.",
            "quantization": "GGUF",
            "decode_read_mb": size_mb,
        }
        append_model(fallback, path, "downloaded")

    if isinstance(loaded_model, str) and loaded_model.strip() and current_model_id is None:
        # An external runtime can report a model that is neither in our catalog
        # nor an inspectable local GGUF. Show what is actually running without
        # borrowing a different quantization's size, fit, or activation claims.
        response_models.append({
            "id": f"runtime-{hashlib.sha256(loaded_model.encode('utf-8')).hexdigest()[:12]}",
            "name": loaded_model,
            "gguf": None,
            "downloadUrl": None,
            "size": None,
            "sizeGb": None,
            "vramRequired": None,
            "estimatedRequired": None,
            "contextLength": context_length,
            "specialty": "Runtime",
            "description": "Reported as loaded by the model runtime; not an ODS catalog or inspected local model.",
            "metadata": {"source": "runtime", "catalogSource": "runtime", "readable": False},
            "appCompatibility": {},
            "status": "loaded",
            "recommended": False,
            "configured": False,
            "fitsVram": None,
            "activationSupport": None,
            "fitsCurrentVram": None,
            "performance": None,
        })

    return {
        "models": response_models,
        "gpu": gpu_data,
        "currentModel": current_model_id,
        "loadedModel": loaded_model,
        "configuredModel": configured_model_id,
        "hermesMinimumContext": HERMES_MIN_CONTEXT,
        "hermesTargetContext": HERMES_TARGET_CONTEXT,
        "pixelMinimumContext": PIXEL_MIN_CONTEXT,
        "recommendationPolicy": recommendation.get("selectionPolicy") or _DEFAULT_RECOMMENDATION_POLICY,
        "recommendationAlternatives": [
            _recommendation_alternative(model, gpu_info)
            for model in ranked_recommendations
        ],
    }
