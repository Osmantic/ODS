"""
Pure settings helpers — regex constants, env parsing, field building, apply planning.

These are leaf functions with no dependency on monkeypatched names (install-root
resolvers, template-path resolvers, cache). Functions that call those resolvers
remain in main.py so that test monkeypatches continue to intercept them.
"""

import re
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from host_agent_client import AgentClientError, request_json as request_agent_json

# ── Regex constants ────────────────────────────────────────────────────────────

_ENV_ASSIGNMENT_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_ENV_COMMENTED_ASSIGNMENT_RE = re.compile(r"^\s*#\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_SENSITIVE_ENV_KEY_RE = re.compile(
    r"(SECRET|TOKEN|PASSWORD|(?:^|_)PASS(?:$|_)|API_KEY|PRIVATE_KEY|ENCRYPTION_KEY|(?:^|_)SALT(?:$|_))"
)

# ── Apply-plan constants ───────────────────────────────────────────────────────

_SETTINGS_APPLY_ALLOWED_SERVICES = frozenset({
    "llama-server", "open-webui", "litellm", "langfuse", "n8n",
    "hermes", "hermes-proxy", "openclaw", "opencode", "perplexica", "searxng", "qdrant",
    "tts", "whisper", "embeddings", "token-spy", "comfyui",
    "ape", "privacy-shield", "ods-proxy", "model-router",
})
_LLAMA_APPLY_KEYS = {
    "CTX_SIZE", "MAX_CONTEXT", "GGUF_FILE", "GGUF_URL", "GGUF_SHA256",
    "LLM_MODEL", "LLM_MODEL_SIZE_MB", "LLM_BACKEND", "N_GPU_LAYERS", "GPU_BACKEND",
    "OLLAMA_PORT", "OLLAMA_URL", "LLM_API_URL", "MODEL_PROFILE",
}
_OPEN_WEBUI_APPLY_KEYS = {
    "ENABLE_IMAGE_GENERATION", "IMAGE_GENERATION_ENGINE", "IMAGE_SIZE",
    "IMAGE_STEPS", "IMAGE_GENERATION_MODEL", "COMFYUI_BASE_URL",
    "COMFYUI_WORKFLOW", "COMFYUI_WORKFLOW_NODES", "AUDIO_STT_ENGINE",
    "AUDIO_STT_OPENAI_API_BASE_URL", "AUDIO_STT_OPENAI_API_KEY",
    "AUDIO_STT_MODEL", "AUDIO_TTS_ENGINE", "AUDIO_TTS_OPENAI_API_BASE_URL",
    "AUDIO_TTS_OPENAI_API_KEY", "AUDIO_TTS_MODEL", "AUDIO_TTS_VOICE",
}
_TOKEN_SPY_APPLY_KEYS = {
    "TOKEN_SPY_URL", "TOKEN_SPY_API_KEY",
}
_PRIVACY_SHIELD_APPLY_KEYS = {
    "TARGET_API_URL", "PII_CACHE_ENABLED", "SHIELD_PORT",
}
_MANUAL_RESTART_KEYS = {
    "BIND_ADDRESS",
    "DASHBOARD_API_KEY", "ODS_AGENT_KEY", "DASHBOARD_PORT",
    "DASHBOARD_API_PORT", "ODS_AGENT_PORT", "ODS_AGENT_HOST",
}
_READ_ONLY_ENV_FIELDS = {
    "ODS_MODE": "Runtime mode is selected by the installer and cannot be changed from the dashboard.",
    "TIER": "The active tier is managed by Model Manager so model consumers stay synchronized.",
    "LLM_MODEL": "The active model is managed by Model Manager so model consumers stay synchronized.",
    "GGUF_FILE": "The active model file is managed by Model Manager so activation remains transactional.",
    "GGUF_URL": "Model artifact metadata is managed by Model Manager.",
    "GGUF_SHA256": "Model integrity metadata is managed by Model Manager.",
    "LEMONADE_MODEL": "The Lemonade model identity is resolved and managed during transactional activation.",
    "MODEL_RUNTIME_PROFILE": "The runtime profile is selected and managed during model activation.",
    "MODEL_RUNTIME_PROFILE_LABEL": "The runtime profile is selected and managed during model activation.",
    "MODEL_RUNTIME_PROFILE_SOURCE": "The runtime profile is selected and managed during model activation.",
}

# ── Env parsing ────────────────────────────────────────────────────────────────


def _strip_env_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _read_env_map_from_path(path: Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    try:
        return _parse_env_text(path.read_text(encoding="utf-8"))
    except OSError:
        return {}, []


def _parse_env_text(raw_text: str) -> tuple[dict[str, str], list[dict[str, Any]]]:
    values: dict[str, str] = {}
    issues: list[dict[str, Any]] = []

    for index, line in enumerate(raw_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        match = _ENV_ASSIGNMENT_RE.match(line)
        if not match:
            issues.append({
                "key": None,
                "line": index,
                "message": "Line is not a valid KEY=value entry.",
            })
            continue

        key, value = match.groups()
        values[key] = _strip_env_quotes(value)

    return values, issues

# ── Value helpers ──────────────────────────────────────────────────────────────


def _normalize_bool(value: Any) -> Optional[str]:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return "true"
    if text in {"false", "0", "no", "off"}:
        return "false"
    return None


def _humanize_env_key(key: str) -> str:
    return key.replace("_", " ").title().replace("Llm", "LLM").replace("Api", "API").replace("Gpu", "GPU")


def _is_secret_field(key: str, definition: Optional[dict[str, Any]] = None) -> bool:
    if definition is not None and "secret" in definition:
        return bool(definition.get("secret"))

    upper_key = key.upper()
    if "PUBLIC_KEY" in upper_key:
        return False
    return bool(_SENSITIVE_ENV_KEY_RE.search(upper_key))


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

# ── Field and form helpers ─────────────────────────────────────────────────────


def _build_env_fields(
    schema_properties: dict[str, Any],
    required_keys: set[str],
    values: dict[str, str],
) -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}

    for key, definition in schema_properties.items():
        field_type = definition.get("type", "string")
        value = values.get(key, "")
        fields[key] = {
            "key": key,
            "label": _humanize_env_key(key),
            "type": field_type,
            "description": definition.get("description", ""),
            "required": key in required_keys,
            "secret": _is_secret_field(key, definition),
            "enum": definition.get("enum", []),
            "default": definition.get("default"),
            "value": value,
            "hasValue": value != "",
            "readOnly": key in _READ_ONLY_ENV_FIELDS,
            "readOnlyReason": _READ_ONLY_ENV_FIELDS.get(key, ""),
        }

    for key, value in values.items():
        if key in fields:
            fields[key]["value"] = value
            fields[key]["hasValue"] = value != ""
            continue
        fields[key] = {
            "key": key,
            "label": _humanize_env_key(key),
            "type": "string",
            "description": "Local override not described by the built-in schema.",
            "required": False,
            "secret": _is_secret_field(key),
            "enum": [],
            "default": None,
            "value": value,
            "hasValue": value != "",
            "readOnly": key in _READ_ONLY_ENV_FIELDS,
            "readOnlyReason": _READ_ONLY_ENV_FIELDS.get(key, ""),
        }

    return fields


def _validate_env_values(
    values: dict[str, str],
    fields: dict[str, dict[str, Any]],
    parse_issues: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    issues = list(parse_issues or [])

    for key, field in fields.items():
        value = values.get(key, "")
        field_type = field.get("type", "string")
        required = field.get("required", False)
        enum_values = field.get("enum") or []

        if value == "":
            if required:
                issues.append({"key": key, "message": "Required value is missing."})
            continue

        if enum_values and value not in enum_values:
            issues.append({"key": key, "message": f"Must be one of: {', '.join(enum_values)}."})
            continue

        if field_type == "integer":
            try:
                int(str(value).strip())
            except (TypeError, ValueError):
                issues.append({"key": key, "message": "Must be a whole number."})
        elif field_type == "boolean":
            if _normalize_bool(value) is None:
                issues.append({"key": key, "message": "Must be true or false."})

    return issues


def _serialize_form_values(
    raw_values: dict[str, Any],
    fields: dict[str, dict[str, Any]],
    current_values: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    serialized: dict[str, str] = {}
    current_values = current_values or {}

    for key, field in fields.items():
        value = raw_values.get(key, current_values.get(key, ""))
        # Reject newlines and null bytes to prevent .env injection
        if value is not None and any(c in str(value) for c in ("\n", "\r", "\0")):
            raise HTTPException(
                status_code=400,
                detail=f"Value for '{key}' contains invalid characters (newlines or null bytes are not allowed)",
            )
        if value is None:
            serialized[key] = current_values.get(key, "") if field.get("secret") else ""
            continue

        field_type = field.get("type", "string")
        if field.get("secret") and str(value).strip() == "":
            serialized[key] = current_values.get(key, "")
            continue
        if field_type == "boolean":
            normalized = _normalize_bool(value)
            serialized[key] = normalized if normalized is not None else str(value).strip()
        elif field_type == "integer":
            serialized[key] = str(value).strip()
        else:
            serialized[key] = str(value)

    return serialized


def _empty_value_unsets_env_key(key: str, field: dict[str, Any]) -> bool:
    """Return true when an empty form value should remove a runtime env key."""
    if field.get("required") or field.get("secret"):
        return False
    return key.startswith("LLAMA_ARG_")

# ── Apply-plan helpers ─────────────────────────────────────────────────────────


def _match_apply_service(key: str) -> Optional[str]:
    if key in _LLAMA_APPLY_KEYS or key.startswith(("LLAMA_", "GGUF_")):
        return "llama-server"
    if key == "SEARXNG_URL":
        return "hermes"
    if (
        key in _OPEN_WEBUI_APPLY_KEYS
        or key.startswith("WEBUI_")
        or key.startswith("OPENAI_API_")
        or key.startswith("SEARXNG_")
    ):
        return "open-webui"
    if key in _TOKEN_SPY_APPLY_KEYS or key.startswith("TOKEN_SPY_"):
        return "token-spy"
    if key in _PRIVACY_SHIELD_APPLY_KEYS or key.startswith("SHIELD_"):
        return "privacy-shield"
    if key.startswith("LITELLM_"):
        return "litellm"
    if key.startswith("LANGFUSE_"):
        return "langfuse"
    if key.startswith("N8N_"):
        return "n8n"
    if key == "ODS_AUTH_UPSTREAM" or key.startswith("HERMES_PROXY_"):
        return "hermes-proxy"
    if key.startswith("HERMES_") or key.startswith("WHATSAPP_"):
        return "hermes"
    if key.startswith("ODS_PROXY_"):
        return "ods-proxy"
    if key.startswith("OPENCLAW_"):
        return "openclaw"
    if key.startswith("COMFYUI_"):
        return "comfyui"
    if key.startswith("WHISPER_"):
        return "whisper"
    if key.startswith("QDRANT_"):
        return "qdrant"
    if key.startswith("TTS_") or key.startswith("KOKORO_"):
        return "tts"
    if key.startswith("EMBEDDINGS_"):
        return "embeddings"
    if key.startswith("PERPLEXICA_"):
        return "perplexica"
    if key.startswith("APE_"):
        return "ape"
    return None


def _build_apply_summary(services: list[str], manual_keys: list[str]) -> str:
    if services and manual_keys:
        return (
            f"Saved changes can be applied now to {', '.join(services)}. "
            f"Other keys still need a broader manual restart: {', '.join(manual_keys)}."
        )
    if services:
        return f"Saved changes are ready to apply to {', '.join(services)}."
    if manual_keys:
        return (
            "Saved changes were written to .env, but these keys still need a manual stack restart: "
            + ", ".join(manual_keys)
            + "."
        )
    return "No service recreation is required for the saved keys."


def _compute_env_apply_plan(previous_values: dict[str, str], next_values: dict[str, str]) -> dict[str, Any]:
    changed_keys = sorted(
        key for key in set(previous_values) | set(next_values)
        if previous_values.get(key, "") != next_values.get(key, "")
    )
    services: set[str] = set()
    manual_keys: list[str] = []

    for key in changed_keys:
        service = _match_apply_service(key)
        if service and service in _SETTINGS_APPLY_ALLOWED_SERVICES:
            services.add(service)
            continue
        if key in _MANUAL_RESTART_KEYS or key.startswith("ODS_AGENT_"):
            manual_keys.append(key)
            continue
        if key not in {"TZ", "TIMEZONE"}:
            manual_keys.append(key)

    services_list = sorted(services)
    manual_list = sorted(set(manual_keys))
    if not changed_keys:
        status = "none"
    elif services_list and manual_list:
        status = "partial"
    elif services_list:
        status = "ready"
    else:
        status = "manual"

    return {
        "status": status,
        "changedKeys": changed_keys,
        "services": services_list,
        "manualKeys": manual_list,
        "supported": bool(services_list),
        "summary": _build_apply_summary(services_list, manual_list),
    }

# ── Agent availability ─────────────────────────────────────────────────────────


def _check_host_agent_available() -> bool:
    try:
        request_agent_json("GET", "/health", timeout=3)
        return True
    except AgentClientError:
        return False
