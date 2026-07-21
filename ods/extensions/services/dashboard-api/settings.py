"""
Pure settings helpers — regex constants, env parsing, field building, apply planning.

These are leaf functions with no dependency on monkeypatched names (install-root
resolvers, template-path resolvers, cache). Functions that call those resolvers
remain in main.py so that test monkeypatches continue to intercept them.
"""

import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import HTTPException

from host_agent_client import AgentClientError, request_json as request_agent_json

# ── Regex constants ────────────────────────────────────────────────────────────

_ENV_ASSIGNMENT_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_ENV_COMMENTED_ASSIGNMENT_RE = re.compile(r"^\s*#\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_SENSITIVE_ENV_KEY_RE = re.compile(
    r"(SECRET|TOKEN|PASSWORD|(?:^|_)PASS(?:$|_)|API_KEY|PRIVATE_KEY|ENCRYPTION_KEY|(?:^|_)SALT(?:$|_))"
)
_GGUF_QUANTIZED_MODEL_RE = re.compile(
    r"(?:^|[-_.])q[2-8](?:_[a-z0-9]+)*(?:$|[-_.])",
    re.IGNORECASE,
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
    "RAG_EMBEDDING_MODEL", "RAG_OPENAI_API_BASE_URL", "RAG_OPENAI_API_KEY",
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


def _is_unsupported_tei_model_id(value: Any) -> bool:
    model_id = str(value).strip().lower()
    artifact_name = model_id.rsplit("/", 1)[-1]
    return (
        "://" in model_id
        or "gguf" in artifact_name
        or "ggml" in artifact_name
        or _GGUF_QUANTIZED_MODEL_RE.search(artifact_name) is not None
    )


def _is_valid_http_endpoint(value: Any) -> bool:
    text = str(value).strip()
    if not text or "\\" in text or any(character.isspace() for character in text):
        return False
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and parsed.fragment == ""
        and (port is None or 1 <= port <= 65535)
    )


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
            "clearable": bool(definition.get("clearable", False)),
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
            "clearable": False,
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

        if key == "EMBEDDING_MODEL":
            if _is_unsupported_tei_model_id(value):
                issues.append({
                    "key": key,
                    "message": (
                        "Bundled embeddings use Hugging Face TEI. Enter a compatible "
                        "Hugging Face repository ID such as BAAI/bge-m3; URLs and "
                        "GGUF/Q4 model artifacts are not supported by this runtime."
                    ),
                })
        elif key == "EMBEDDINGS_MEMORY_LIMIT":
            if not re.fullmatch(r"[1-9][0-9]*(?:[bBkKmMgG]|[kKmMgG][bB])?", str(value).strip()):
                issues.append({
                    "key": key,
                    "message": "Must be a positive Docker memory value such as 4096M, 4G, or 6GB.",
                })
        elif key == "RAG_OPENAI_API_BASE_URL":
            if not _is_valid_http_endpoint(value):
                issues.append({
                    "key": key,
                    "message": "Must be an HTTP(S) OpenAI-compatible embeddings base URL.",
                })

    embedding_model = str(values.get("EMBEDDING_MODEL", "")).strip() or "BAAI/bge-base-en-v1.5"
    rag_model = str(values.get("RAG_EMBEDDING_MODEL", "")).strip()
    rag_base = str(values.get("RAG_OPENAI_API_BASE_URL", "")).strip().lower().rstrip("/")
    bundled_rag_bases = {
        "",
        "http://embeddings:80/v1",
        "http://embeddings/v1",
        "http://ods-embeddings:80/v1",
        "http://ods-embeddings/v1",
    }
    if rag_model and embedding_model and rag_base in bundled_rag_bases and rag_model != embedding_model:
        issues.append({
            "key": "RAG_EMBEDDING_MODEL",
            "message": (
                "Bundled TEI serves EMBEDDING_MODEL only. Leave this override empty "
                "to inherit it, or set RAG_OPENAI_API_BASE_URL to the external "
                "provider that serves this different model."
            ),
        })

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
    return key.startswith("LLAMA_ARG_") or key in {
        "RAG_EMBEDDING_MODEL",
        "RAG_OPENAI_API_BASE_URL",
    }

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
    if key.startswith("RAG_"):
        return "open-webui"
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


def _build_apply_summary(
    services: list[str],
    manual_keys: list[str],
    inactive_services: Optional[list[str]] = None,
) -> str:
    inactive_services = inactive_services or []
    parts: list[str] = []
    if services:
        parts.append(f"Saved changes are ready to apply to {', '.join(services)}.")
    if manual_keys:
        parts.append(f"A manual stack restart is still required for: {', '.join(manual_keys)}.")
    if inactive_services:
        parts.append(
            "Configuration was staged for disabled services and will apply when they are enabled: "
            + ", ".join(inactive_services)
            + "."
        )
    return " ".join(parts) or "No service recreation is required for the saved keys."


def _compute_env_apply_plan(
    previous_values: dict[str, str],
    next_values: dict[str, str],
    active_services: Optional[set[str]] = None,
) -> dict[str, Any]:
    changed_keys = sorted(
        key for key in set(previous_values) | set(next_values)
        if previous_values.get(key, "") != next_values.get(key, "")
    )
    services: set[str] = set()
    inactive_services: set[str] = set()
    manual_keys: list[str] = []
    rag_admin_sync_required = False
    rag_reindex_required = False

    next_rag_model = str(next_values.get("RAG_EMBEDDING_MODEL", "")).strip()
    # Compose falls back to EMBEDDING_MODEL whenever RAG_EMBEDDING_MODEL is
    # empty, regardless of whether the selected endpoint is bundled or external.
    open_webui_inherits_embedding_model = not next_rag_model

    def schedule(service_id: str) -> bool:
        if active_services is None or service_id in active_services:
            services.add(service_id)
            return True
        inactive_services.add(service_id)
        return False

    for key in changed_keys:
        if key == "EMBEDDING_MODEL":
            schedule("embeddings")
            if open_webui_inherits_embedding_model:
                schedule("open-webui")
                rag_admin_sync_required = True
                rag_reindex_required = True
            continue
        if key in {"RAG_EMBEDDING_MODEL", "RAG_OPENAI_API_BASE_URL"}:
            schedule("open-webui")
            rag_admin_sync_required = True
            rag_reindex_required = True
            continue
        if key == "RAG_OPENAI_API_KEY":
            schedule("open-webui")
            rag_admin_sync_required = True
            continue
        service = _match_apply_service(key)
        if service and service in _SETTINGS_APPLY_ALLOWED_SERVICES:
            schedule(service)
            continue
        if key in _MANUAL_RESTART_KEYS or key.startswith("ODS_AGENT_"):
            manual_keys.append(key)
            continue
        if key not in {"TZ", "TIMEZONE"}:
            manual_keys.append(key)

    services_list = sorted(services)
    inactive_list = sorted(inactive_services)
    manual_list = sorted(set(manual_keys))
    if not changed_keys:
        status = "none"
    elif services_list and (manual_list or inactive_list):
        status = "partial"
    elif services_list:
        status = "ready"
    elif manual_list:
        status = "manual"
    elif inactive_list:
        status = "staged"
    else:
        status = "manual"

    post_apply_actions = []
    if rag_admin_sync_required:
        post_apply_actions.append({
            "id": "open-webui-rag-sync",
            "title": "Apply RAG settings in Open WebUI",
            "message": (
                "After Open WebUI is healthy, open Admin Panel / Settings / "
                "Documents and set the embedding engine, endpoint, model, and "
                "credential to the saved values. Open WebUI persists these settings "
                "in its database after first boot."
            ),
        })
    if rag_reindex_required:
        post_apply_actions.append({
            "id": "open-webui-rag-reindex",
            "title": "Reindex Open WebUI knowledge bases",
            "message": (
                "Run Reindex after applying the new model or endpoint. Files attached "
                "directly to old chats must be uploaded again because embeddings from "
                "different models are not interchangeable."
            ),
        })

    return {
        "status": status,
        "changedKeys": changed_keys,
        "services": services_list,
        "inactiveServices": inactive_list,
        "manualKeys": manual_list,
        "supported": bool(services_list),
        "summary": _build_apply_summary(services_list, manual_list, inactive_list),
        "postApplyActions": post_apply_actions,
    }

# ── Agent availability ─────────────────────────────────────────────────────────


def _check_host_agent_available() -> bool:
    try:
        request_agent_json("GET", "/health", timeout=3)
        return True
    except AgentClientError:
        return False
