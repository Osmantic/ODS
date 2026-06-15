"""Capability contract probes for Lemonade-backed provider mode."""

from __future__ import annotations

import math
from typing import Any, Callable, Literal, NamedTuple, Optional
from urllib.parse import urlsplit

from lemonade_client import LemonadeClient, LemonadeClientError, LemonadeSettings, normalize_base_url


EnvReader = Callable[[str], str]
CapabilityPayload = dict[str, object]


class ExternalLemonadeProbeResult(NamedTuple):
    health: str
    version: str
    warnings: list[str]
    loaded_model: Optional[str]
    model_count: Optional[int]
    capabilities: list[CapabilityPayload]
    probe_mode: Literal["passive", "active"]
    loaded_models: list[CapabilityPayload]

DEFAULT_EXTERNAL_LEMONADE_PROBE_TTL = 120.0
DEFAULT_EXTERNAL_LEMONADE_ACTIVE_PROBE_TIMEOUT = 120.0
SPECIALIZED_MODEL_LABELS = frozenset({"embeddings", "reranking", "transcription", "image", "edit", "tts"})
SPECIALIZED_MODEL_ID_MARKERS = (
    "whisper",
    "moonshine",
    "kokoro",
    "embedding",
    "embed-",
    "rerank",
    "stable-diffusion",
    "sdxl",
    "flux",
    "image",
)
CAPABILITY_MODEL_LABELS = {
    "embeddings": "embeddings",
    "rerank": "reranking",
    "stt": "transcription",
    "tts": "tts",
}
HEALTH_OK_STATUSES = frozenset({"ok", "healthy", "ready"})
TRACKED_EXTERNAL_LEMONADE_ENV_KEYS = (
    "LEMONADE_MODEL",
    "LEMONADE_API_KEY",
    "LITELLM_LEMONADE_API_KEY",
    "LITELLM_KEY",
    "OPENAI_API_KEY",
    "LLM_URL",
    "LLM_API_URL",
    "LLM_BACKEND",
    "LLM_MODEL",
    "ENABLE_RAG",
    "ENABLE_EMBEDDINGS",
    "ENABLE_VOICE",
    "ENABLE_RERANK",
    "ENABLE_RERANKING",
    "EMBEDDING_URL",
    "EMBEDDING_API_BASE_URL",
    "EMBEDDING_MODEL",
    "LEMONADE_EMBEDDING_MODEL",
    "LEMONADE_RERANK_MODEL",
    "RERANK_MODEL",
    "WHISPER_URL",
    "TTS_URL",
    "KOKORO_URL",
    "AUDIO_STT_OPENAI_API_BASE_URL",
    "AUDIO_TTS_OPENAI_API_BASE_URL",
    "AUDIO_STT_MODEL",
    "AUDIO_TTS_MODEL",
    "AUDIO_TTS_VOICE",
    "LEMONADE_STT_MODEL",
    "LEMONADE_TTS_MODEL",
    "DASHBOARD_LEMONADE_PROBE_TTL",
    "DASHBOARD_LEMONADE_ACTIVE_PROBE_TIMEOUT",
)


def external_lemonade_probe_ttl(env_get: EnvReader) -> float:
    raw = env_get("DASHBOARD_LEMONADE_PROBE_TTL")
    if not raw:
        return DEFAULT_EXTERNAL_LEMONADE_PROBE_TTL
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_EXTERNAL_LEMONADE_PROBE_TTL
    return value if math.isfinite(value) and value > 0 else DEFAULT_EXTERNAL_LEMONADE_PROBE_TTL


def external_lemonade_active_probe_timeout(env_get: EnvReader) -> float:
    raw = env_get("DASHBOARD_LEMONADE_ACTIVE_PROBE_TIMEOUT")
    if not raw:
        return DEFAULT_EXTERNAL_LEMONADE_ACTIVE_PROBE_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_EXTERNAL_LEMONADE_ACTIVE_PROBE_TIMEOUT
    return value if math.isfinite(value) and value > 0 else DEFAULT_EXTERNAL_LEMONADE_ACTIVE_PROBE_TIMEOUT


def external_lemonade_probe_cache_key(api_base: str, api_path: str, env_get: EnvReader) -> tuple[str, ...]:
    return (
        api_base,
        api_path,
        *(env_get(name) for name in TRACKED_EXTERNAL_LEMONADE_ENV_KEYS),
    )


def _env_bool(env_get: EnvReader, name: str) -> bool:
    return env_get(name).lower() in {"1", "true", "yes", "on"}


def _feature_selected(env_get: EnvReader, *names: str) -> bool:
    return any(_env_bool(env_get, name) for name in names)


def _model_env(env_get: EnvReader, *names: str) -> Optional[str]:
    for name in names:
        value = env_get(name)
        if value and value not in {"*", "default"}:
            return value
    return None


def _external_lemonade_warning(prefix: str, exc: LemonadeClientError) -> str:
    if exc.kind == "provider_unreachable":
        return f"{prefix}_unreachable"
    return f"{prefix}_{exc.kind}"


def _loaded_model_from_health(payload: dict) -> Optional[str]:
    for key in ("model_loaded", "loaded_model", "active_model", "model"):
        value = payload.get(key)
        if value:
            return str(value)
    loaded_models = _loaded_models_from_health(payload)
    for loaded_model in loaded_models:
        if loaded_model.get("type") == "llm":
            return str(loaded_model["modelName"])
    if loaded_models:
        return str(loaded_models[0]["modelName"])
    return None


def _health_status(payload: dict) -> str:
    status = payload.get("status")
    if not isinstance(status, str) or not status.strip():
        return "invalid_response"
    return status.strip().lower()


def _loaded_models_from_health(payload: dict) -> list[CapabilityPayload]:
    loaded_models = payload.get("all_models_loaded")
    if not isinstance(loaded_models, list):
        return []

    results: list[CapabilityPayload] = []
    for entry in loaded_models:
        if not isinstance(entry, dict) or not entry.get("model_name"):
            continue
        result: CapabilityPayload = {
            "modelName": str(entry["model_name"]),
            "type": str(entry.get("type") or "unknown").lower(),
        }
        for source, target in (
            ("device", "device"),
            ("recipe", "recipe"),
            ("backend_url", "backendUrl"),
        ):
            if entry.get(source):
                result[target] = str(entry[source])
        results.append(result)
    return results


def _model_id_from_entry(entry: dict) -> Optional[str]:
    for key in ("id", "name", "model"):
        value = entry.get(key)
        if value:
            return str(value)
    return None


def _model_labels(entry: dict) -> set[str]:
    labels = entry.get("labels")
    if not isinstance(labels, list):
        return set()
    return {str(label).strip().lower() for label in labels if str(label).strip()}


def _model_entry(model_entries: list[dict], model: str) -> Optional[dict]:
    for entry in model_entries:
        if _model_id_from_entry(entry) == model:
            return entry
    return None


def _model_is_chat_capable(entry: dict) -> bool:
    model_id = (_model_id_from_entry(entry) or "").lower()
    recipe = str(entry.get("recipe") or "").lower()
    specialized_recipe = recipe in {"sd-cpp", "whisper", "whispercpp", "kokoro", "kokoros"}
    return not (
        _model_labels(entry) & SPECIALIZED_MODEL_LABELS
        or specialized_recipe
        or any(marker in model_id for marker in SPECIALIZED_MODEL_ID_MARKERS)
    )


def _legacy_lemonade_chat_model(env_get: EnvReader, model_entries: list[dict]) -> Optional[str]:
    legacy_model = _model_env(env_get, "LLM_MODEL")
    if not legacy_model:
        return None
    legacy_entry = _model_entry(model_entries, legacy_model)
    if legacy_entry is None or not _model_is_chat_capable(legacy_entry):
        return None
    return legacy_model


def _select_lemonade_probe_model(
    env_get: EnvReader,
    model_entries: list[dict],
    loaded_model: Optional[str],
) -> Optional[str]:
    explicit_model = env_get("LEMONADE_MODEL")
    if explicit_model and explicit_model not in {"*", "default"}:
        return explicit_model
    legacy_model = _legacy_lemonade_chat_model(env_get, model_entries)
    if legacy_model:
        return legacy_model
    if loaded_model:
        loaded_entry = _model_entry(model_entries, loaded_model)
        if loaded_entry is None or _model_is_chat_capable(loaded_entry):
            return loaded_model
    for entry in model_entries:
        model_id = _model_id_from_entry(entry)
        if model_id and _model_is_chat_capable(entry):
            return model_id
    return None


def _chat_completion_ready(payload: dict) -> bool:
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return False
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return True
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and str(block.get("text") or "").strip():
                        return True
        text = choice.get("text")
        if isinstance(text, str) and text.strip():
            return True
    return False


def _clean_api_path(path: str) -> str:
    cleaned = f"/{str(path or '/api/v1').strip('/')}"
    return cleaned.rstrip("/") or "/"


def _url_points_to_provider(url: str, api_base: str, api_path: str) -> bool:
    if not url:
        return False
    provider = urlsplit(normalize_base_url(api_base, api_path))
    candidate = urlsplit(url.strip())
    if not candidate.scheme or not candidate.netloc:
        candidate = urlsplit(normalize_base_url(url, api_path))

    if (candidate.scheme.lower(), candidate.netloc.lower()) != (provider.scheme.lower(), provider.netloc.lower()):
        return False

    candidate_path = _clean_api_path(candidate.path or "/")
    if candidate_path == "/":
        return True

    api_paths = {_clean_api_path(api_path), "/api/v1", "/v1"}
    return any(candidate_path == path or candidate_path.startswith(f"{path}/") for path in api_paths)


def _capability_service_owner(
    env_get: EnvReader,
    url_names: tuple[str, ...],
    api_base: str,
    api_path: str,
    service_name: str,
) -> Optional[str]:
    for name in url_names:
        url = env_get(name)
        if not url:
            continue
        if not _url_points_to_provider(url, api_base, api_path):
            return f"handled_by_{service_name}"
        return None
    return None


def _recovery_hint(name: str, status: str, detail: Optional[str], required: bool) -> Optional[str]:
    if status in {"ok", "skipped"} or (name == "stats" and status == "unsupported"):
        return None
    if status == "unverified":
        return "Run the active Lemonade probe to verify this configured route. It may load or switch models."
    if detail == "model_not_downloaded":
        return "Pull the configured model in Lemonade, then refresh the provider contract."
    if name == "health":
        if detail in {"provider_unreachable", "timeout"}:
            return "Start Lemonade and verify LEMONADE_CONTAINER_BASE_URL is reachable from dashboard-api."
        if detail == "auth_rejected":
            return "Set LEMONADE_API_KEY or LITELLM_LEMONADE_API_KEY to the bearer key accepted by Lemonade."
    if name == "models":
        return "Load at least one Lemonade model and verify the /models endpoint returns it."
    if name == "chat":
        if detail == "model_not_chat":
            return "Set LEMONADE_MODEL to a text/chat model instead of a specialized audio, image, embedding, or rerank model."
        return "Set LEMONADE_MODEL to a model id returned by Lemonade /models, then retry the readiness probe."
    if name == "gateway_chat":
        if detail == "auth_rejected":
            return "Set LITELLM_KEY to the LiteLLM master key and verify LiteLLM routes to Lemonade."
        return "Verify LLM_URL or LLM_API_URL points to LiteLLM and that LiteLLM can call Lemonade."
    if name == "embeddings":
        return "Set LEMONADE_EMBEDDING_MODEL or route RAG embeddings to Dream's embeddings service."
    if name == "rerank":
        return "Set LEMONADE_RERANK_MODEL to a rerank-capable model or disable reranking for this profile."
    if name == "stt":
        return "Set LEMONADE_STT_MODEL or route voice input to Dream's Whisper service."
    if name == "tts":
        return "Set LEMONADE_TTS_MODEL or route voice output to Dream's TTS service."
    if required:
        return "Fix the selected provider capability or disable the feature that requires it."
    return None


def provider_capability_summary(capabilities: list[CapabilityPayload]) -> tuple[Optional[bool], str]:
    required_capabilities = [
        capability
        for capability in capabilities
        if capability.get("required") is True
    ]
    if not required_capabilities:
        return False, "blocked"

    if any(
        capability.get("status") in {"failed", "unsupported"}
        for capability in required_capabilities
    ):
        return False, "blocked"
    if any(
        capability.get("status") in {"skipped", "unverified"}
        for capability in required_capabilities
    ):
        return None, "unverified"

    optional_failed = any(
        capability.get("status") == "failed"
        for capability in capabilities
        if capability.get("required") is not True
    )
    return True, "degraded" if optional_failed else "ready"


def _capability_status(
    name: str,
    status: str,
    detail: Optional[str] = None,
    *,
    required: bool = False,
) -> CapabilityPayload:
    result: CapabilityPayload = {"name": name, "status": status, "required": required}
    if detail:
        result["detail"] = detail[:160]
    hint = _recovery_hint(name, status, detail, required)
    if hint:
        result["recoveryHint"] = hint[:240]
    return result


def _replace_capability(
    capabilities: list[CapabilityPayload],
    name: str,
    status: str,
    detail: Optional[str] = None,
    *,
    required: bool = False,
) -> None:
    replacement = _capability_status(name, status, detail, required=required)
    for index, capability in enumerate(capabilities):
        if capability.get("name") == name:
            capabilities[index] = replacement
            return
    capabilities.append(replacement)


def _silence_wav_bytes() -> bytes:
    sample_rate = 16000
    frames = sample_rate // 10
    data_size = frames * 2
    byte_rate = sample_rate * 2
    block_align = 2
    return (
        b"RIFF"
        + (36 + data_size).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + sample_rate.to_bytes(4, "little")
        + byte_rate.to_bytes(4, "little")
        + block_align.to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + data_size.to_bytes(4, "little")
        + (b"\x00" * data_size)
    )


def _passive_model_capability(
    name: str,
    model: Optional[str],
    model_entries: list[dict],
    loaded_models: list[CapabilityPayload],
    warnings: list[str],
    *,
    models_verified: bool,
) -> CapabilityPayload:
    if not model:
        warnings.append(f"{name}_model_missing")
        return _capability_status(name, "failed", "model_missing", required=True)

    if name == "chat":
        expected_type = "llm"
    else:
        expected_type = {
            "embeddings": "embedding",
            "rerank": "reranking",
            "stt": "transcription",
            "tts": "tts",
        }[name]

    if any(
        loaded.get("modelName") == model and loaded.get("type") == expected_type
        for loaded in loaded_models
    ):
        return _capability_status(name, "ok", model, required=True)

    if not models_verified:
        return _capability_status(name, "unverified", "models_unavailable", required=True)

    entry = _model_entry(model_entries, model)
    if entry is None:
        warnings.append(f"{name}_model_not_found")
        return _capability_status(name, "failed", "model_not_found", required=True)
    if entry.get("downloaded") is False:
        warnings.append(f"{name}_model_not_downloaded")
        return _capability_status(name, "failed", "model_not_downloaded", required=True)

    if name == "chat" and not _model_is_chat_capable(entry):
        warnings.append("chat_model_not_chat")
        return _capability_status(name, "unsupported", "model_not_chat", required=True)

    if name != "chat" and CAPABILITY_MODEL_LABELS[name] not in _model_labels(entry):
        return _capability_status(name, "unverified", "model_label_missing", required=True)
    return _capability_status(name, "unverified", model, required=True)


def _describe_embedding_capability(
    capabilities: list[CapabilityPayload],
    warnings: list[str],
    env_get: EnvReader,
    api_base: str,
    api_path: str,
    model_entries: list[dict],
    loaded_models: list[CapabilityPayload],
    models_verified: bool,
) -> None:
    explicit_model = _model_env(env_get, "LEMONADE_EMBEDDING_MODEL")
    selected = _feature_selected(env_get, "ENABLE_EMBEDDINGS", "ENABLE_RAG") or bool(explicit_model)
    if not selected:
        capabilities.append(_capability_status("embeddings", "skipped", "not_selected"))
        return

    owner = _capability_service_owner(
        env_get,
        ("EMBEDDING_URL", "EMBEDDING_API_BASE_URL"),
        api_base,
        api_path,
        "embeddings_service",
    )
    if owner and not explicit_model:
        capabilities.append(_capability_status("embeddings", "skipped", owner))
        return

    model = explicit_model or _model_env(env_get, "EMBEDDING_MODEL")
    capabilities.append(
        _passive_model_capability(
            "embeddings",
            model,
            model_entries,
            loaded_models,
            warnings,
            models_verified=models_verified,
        )
    )


def _describe_rerank_capability(
    capabilities: list[CapabilityPayload],
    warnings: list[str],
    env_get: EnvReader,
    fallback_model: Optional[str],
    model_entries: list[dict],
    loaded_models: list[CapabilityPayload],
    models_verified: bool,
) -> None:
    explicit_model = _model_env(env_get, "LEMONADE_RERANK_MODEL", "RERANK_MODEL")
    selected = _feature_selected(env_get, "ENABLE_RERANK", "ENABLE_RERANKING") or bool(explicit_model)
    if not selected:
        capabilities.append(_capability_status("rerank", "skipped", "not_selected"))
        return
    capabilities.append(
        _passive_model_capability(
            "rerank",
            explicit_model or fallback_model,
            model_entries,
            loaded_models,
            warnings,
            models_verified=models_verified,
        )
    )


def _describe_stt_capability(
    capabilities: list[CapabilityPayload],
    warnings: list[str],
    env_get: EnvReader,
    api_base: str,
    api_path: str,
    model_entries: list[dict],
    loaded_models: list[CapabilityPayload],
    models_verified: bool,
) -> None:
    explicit_model = _model_env(env_get, "LEMONADE_STT_MODEL")
    selected = _feature_selected(env_get, "ENABLE_VOICE") or bool(explicit_model)
    if not selected:
        capabilities.append(_capability_status("stt", "skipped", "not_selected"))
        return

    owner = _capability_service_owner(
        env_get,
        ("WHISPER_URL", "AUDIO_STT_OPENAI_API_BASE_URL"),
        api_base,
        api_path,
        "whisper_service",
    )
    if owner and not explicit_model:
        capabilities.append(_capability_status("stt", "skipped", owner))
        return

    model = explicit_model or _model_env(env_get, "AUDIO_STT_MODEL")
    capabilities.append(
        _passive_model_capability(
            "stt",
            model,
            model_entries,
            loaded_models,
            warnings,
            models_verified=models_verified,
        )
    )


def _describe_tts_capability(
    capabilities: list[CapabilityPayload],
    warnings: list[str],
    env_get: EnvReader,
    api_base: str,
    api_path: str,
    model_entries: list[dict],
    loaded_models: list[CapabilityPayload],
    models_verified: bool,
) -> None:
    explicit_model = _model_env(env_get, "LEMONADE_TTS_MODEL")
    selected = _feature_selected(env_get, "ENABLE_VOICE") or bool(explicit_model)
    if not selected:
        capabilities.append(_capability_status("tts", "skipped", "not_selected"))
        return

    owner = _capability_service_owner(
        env_get,
        ("TTS_URL", "KOKORO_URL", "AUDIO_TTS_OPENAI_API_BASE_URL"),
        api_base,
        api_path,
        "tts_service",
    )
    if owner and not explicit_model:
        capabilities.append(_capability_status("tts", "skipped", owner))
        return

    model = explicit_model or _model_env(env_get, "AUDIO_TTS_MODEL")
    capabilities.append(
        _passive_model_capability(
            "tts",
            model,
            model_entries,
            loaded_models,
            warnings,
            models_verified=models_verified,
        )
    )


def _describe_selected_lemonade_capabilities(
    capabilities: list[CapabilityPayload],
    warnings: list[str],
    env_get: EnvReader,
    api_base: str,
    api_path: str,
    fallback_model: Optional[str],
    model_entries: list[dict],
    loaded_models: list[CapabilityPayload],
    models_verified: bool,
) -> None:
    if _gateway_base_url(env_get, api_base, api_path):
        capabilities.append(_capability_status("gateway_chat", "unverified", "active_probe_required", required=True))
    _describe_embedding_capability(
        capabilities, warnings, env_get, api_base, api_path, model_entries, loaded_models, models_verified
    )
    _describe_rerank_capability(
        capabilities, warnings, env_get, fallback_model, model_entries, loaded_models, models_verified
    )
    _describe_stt_capability(
        capabilities, warnings, env_get, api_base, api_path, model_entries, loaded_models, models_verified
    )
    _describe_tts_capability(
        capabilities, warnings, env_get, api_base, api_path, model_entries, loaded_models, models_verified
    )


async def _probe_embedding_capability(
    client: LemonadeClient,
    capabilities: list[CapabilityPayload],
    warnings: list[str],
    env_get: EnvReader,
    api_base: str,
    api_path: str,
) -> None:
    explicit_model = _model_env(env_get, "LEMONADE_EMBEDDING_MODEL")
    selected = _feature_selected(env_get, "ENABLE_EMBEDDINGS") or _feature_selected(env_get, "ENABLE_RAG")
    selected = selected or bool(explicit_model)
    if not selected:
        capabilities.append(_capability_status("embeddings", "skipped", "not_selected"))
        return

    owner = _capability_service_owner(
        env_get,
        ("EMBEDDING_URL", "EMBEDDING_API_BASE_URL"),
        api_base,
        api_path,
        "embeddings_service",
    )
    if owner and not explicit_model:
        capabilities.append(_capability_status("embeddings", "skipped", owner))
        return

    model = explicit_model or _model_env(env_get, "EMBEDDING_MODEL")
    if not model:
        warnings.append("embeddings_model_missing")
        capabilities.append(_capability_status("embeddings", "failed", "model_missing", required=True))
        return

    try:
        payload = await client.embeddings(model, "ping")
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("embedding"):
            capabilities.append(_capability_status("embeddings", "ok", model, required=True))
        else:
            warnings.append("embeddings_invalid_response")
            capabilities.append(_capability_status("embeddings", "failed", "invalid_response", required=True))
    except LemonadeClientError as exc:
        warnings.append(_external_lemonade_warning("embeddings", exc))
        status = "unsupported" if exc.kind == "not_found" else "failed"
        capabilities.append(_capability_status("embeddings", status, exc.kind, required=True))


async def _probe_rerank_capability(
    client: LemonadeClient,
    capabilities: list[CapabilityPayload],
    warnings: list[str],
    env_get: EnvReader,
    fallback_model: Optional[str],
) -> None:
    explicit_model = _model_env(env_get, "LEMONADE_RERANK_MODEL", "RERANK_MODEL")
    selected = _feature_selected(env_get, "ENABLE_RERANK", "ENABLE_RERANKING") or bool(explicit_model)
    if not selected:
        capabilities.append(_capability_status("rerank", "skipped", "not_selected"))
        return

    model = explicit_model or fallback_model
    if not model:
        warnings.append("rerank_model_missing")
        capabilities.append(_capability_status("rerank", "failed", "model_missing", required=True))
        return

    try:
        payload = await client.rerank(model, "ping", ["ping", "pong"])
        results = payload.get("results") if isinstance(payload, dict) else None
        if isinstance(results, list) and results:
            capabilities.append(_capability_status("rerank", "ok", model, required=True))
        else:
            warnings.append("rerank_invalid_response")
            capabilities.append(_capability_status("rerank", "failed", "invalid_response", required=True))
    except LemonadeClientError as exc:
        warnings.append(_external_lemonade_warning("rerank", exc))
        status = "unsupported" if exc.kind == "not_found" else "failed"
        capabilities.append(_capability_status("rerank", status, exc.kind, required=True))


async def _probe_stt_capability(
    client: LemonadeClient,
    capabilities: list[CapabilityPayload],
    warnings: list[str],
    env_get: EnvReader,
    api_base: str,
    api_path: str,
) -> None:
    explicit_model = _model_env(env_get, "LEMONADE_STT_MODEL")
    selected = _feature_selected(env_get, "ENABLE_VOICE") or bool(explicit_model)
    if not selected:
        capabilities.append(_capability_status("stt", "skipped", "not_selected"))
        return

    owner = _capability_service_owner(
        env_get,
        ("WHISPER_URL", "AUDIO_STT_OPENAI_API_BASE_URL"),
        api_base,
        api_path,
        "whisper_service",
    )
    if owner and not explicit_model:
        capabilities.append(_capability_status("stt", "skipped", owner))
        return

    model = explicit_model or _model_env(env_get, "AUDIO_STT_MODEL")
    if not model:
        warnings.append("stt_model_missing")
        capabilities.append(_capability_status("stt", "failed", "model_missing", required=True))
        return

    try:
        payload = await client.transcribe_wav(model, _silence_wav_bytes(), filename="dream-probe.wav")
        if isinstance(payload, dict) and isinstance(payload.get("text"), str):
            capabilities.append(_capability_status("stt", "ok", model, required=True))
        else:
            warnings.append("stt_invalid_response")
            capabilities.append(_capability_status("stt", "failed", "invalid_response", required=True))
    except LemonadeClientError as exc:
        warnings.append(_external_lemonade_warning("stt", exc))
        status = "unsupported" if exc.kind == "not_found" else "failed"
        capabilities.append(_capability_status("stt", status, exc.kind, required=True))


async def _probe_tts_capability(
    client: LemonadeClient,
    capabilities: list[CapabilityPayload],
    warnings: list[str],
    env_get: EnvReader,
    api_base: str,
    api_path: str,
) -> None:
    explicit_model = _model_env(env_get, "LEMONADE_TTS_MODEL")
    selected = _feature_selected(env_get, "ENABLE_VOICE") or bool(explicit_model)
    if not selected:
        capabilities.append(_capability_status("tts", "skipped", "not_selected"))
        return

    owner = _capability_service_owner(
        env_get,
        ("TTS_URL", "KOKORO_URL", "AUDIO_TTS_OPENAI_API_BASE_URL"),
        api_base,
        api_path,
        "tts_service",
    )
    if owner and not explicit_model:
        capabilities.append(_capability_status("tts", "skipped", owner))
        return

    model = explicit_model or _model_env(env_get, "AUDIO_TTS_MODEL") or "kokoro-v1"
    voice = env_get("AUDIO_TTS_VOICE") or "af_heart"
    try:
        audio = await client.speech(model, "ping", voice=voice)
        if audio:
            capabilities.append(_capability_status("tts", "ok", model, required=True))
        else:
            warnings.append("tts_invalid_response")
            capabilities.append(_capability_status("tts", "failed", "invalid_response", required=True))
    except LemonadeClientError as exc:
        warnings.append(_external_lemonade_warning("tts", exc))
        status = "unsupported" if exc.kind == "not_found" else "failed"
        capabilities.append(_capability_status("tts", status, exc.kind, required=True))


def _gateway_base_url(env_get: EnvReader, api_base: str, api_path: str) -> Optional[str]:
    if env_get("LLM_BACKEND").lower() != "lemonade":
        return None
    for name in ("LLM_URL", "LLM_API_URL"):
        url = env_get(name)
        if url and not _url_points_to_provider(url, api_base, api_path):
            return url
    return None


async def _probe_gateway_chat_capability(
    capabilities: list[CapabilityPayload],
    warnings: list[str],
    env_get: EnvReader,
    api_base: str,
    api_path: str,
    model: Optional[str],
    client_cls: Any,
) -> None:
    gateway_base = _gateway_base_url(env_get, api_base, api_path)
    if not gateway_base:
        return

    if not model:
        warnings.append("gateway_chat_model_missing")
        capabilities.append(_capability_status("gateway_chat", "failed", "model_missing", required=True))
        return

    settings = LemonadeSettings(
        base_url=normalize_base_url(gateway_base, "/v1"),
        api_base_path="/v1",
        api_key=env_get("LITELLM_KEY") or env_get("OPENAI_API_KEY"),
        timeout=external_lemonade_active_probe_timeout(env_get),
    )
    async with client_cls(settings=settings) as client:
        try:
            completion = await client.chat_completion(
                "default",
                [{"role": "user", "content": "ping"}],
                max_tokens=1,
                stream=False,
                extra_body={"temperature": 0},
            )
            if _chat_completion_ready(completion):
                capabilities.append(_capability_status("gateway_chat", "ok", "default", required=True))
            else:
                warnings.append("gateway_chat_invalid_response")
                capabilities.append(_capability_status("gateway_chat", "failed", "invalid_response", required=True))
        except LemonadeClientError as exc:
            warnings.append(_external_lemonade_warning("gateway_chat", exc))
            capabilities.append(_capability_status("gateway_chat", "failed", exc.kind, required=True))


async def _probe_selected_lemonade_capabilities(
    client: LemonadeClient,
    capabilities: list[CapabilityPayload],
    warnings: list[str],
    env_get: EnvReader,
    api_base: str,
    api_path: str,
    fallback_model: Optional[str],
    client_cls: Any,
) -> None:
    await _probe_gateway_chat_capability(capabilities, warnings, env_get, api_base, api_path, fallback_model, client_cls)
    await _probe_embedding_capability(client, capabilities, warnings, env_get, api_base, api_path)
    await _probe_rerank_capability(client, capabilities, warnings, env_get, fallback_model)
    await _probe_stt_capability(client, capabilities, warnings, env_get, api_base, api_path)
    await _probe_tts_capability(client, capabilities, warnings, env_get, api_base, api_path)


async def probe_external_lemonade_uncached(
    api_base: str,
    api_path: str,
    env_get: EnvReader,
    *,
    active: bool = False,
    client_cls: Any = LemonadeClient,
) -> ExternalLemonadeProbeResult:
    probe_mode: Literal["passive", "active"] = "active" if active else "passive"
    settings = LemonadeSettings(
        base_url=normalize_base_url(api_base, api_path),
        api_base_path=api_path,
        api_key=env_get("LEMONADE_API_KEY") or env_get("LITELLM_LEMONADE_API_KEY"),
        timeout=external_lemonade_active_probe_timeout(env_get) if active else 2.0,
    )
    warnings: list[str] = []
    provider_capabilities: list[CapabilityPayload] = []

    async with client_cls(settings=settings) as client:
        try:
            health_payload = await client.health()
        except LemonadeClientError as exc:
            status = "unreachable" if exc.kind in {"provider_unreachable", "timeout"} else "unhealthy"
            provider_capabilities.append(_capability_status("health", "failed", exc.kind, required=True))
            return ExternalLemonadeProbeResult(
                status,
                "unknown",
                [_external_lemonade_warning("health", exc)],
                None,
                None,
                provider_capabilities,
                probe_mode,
                [],
            )

        version = str(health_payload.get("version") or "unknown")
        loaded_model = _loaded_model_from_health(health_payload)
        loaded_models = _loaded_models_from_health(health_payload)
        health_status = _health_status(health_payload)
        if health_status not in HEALTH_OK_STATUSES:
            warnings.append("health_invalid_response" if health_status == "invalid_response" else "health_unhealthy")
            provider_capabilities.append(_capability_status("health", "failed", health_status, required=True))
            return ExternalLemonadeProbeResult(
                "unhealthy",
                version,
                warnings,
                loaded_model,
                None,
                provider_capabilities,
                probe_mode,
                loaded_models,
            )
        else:
            provider_capabilities.append(_capability_status("health", "ok", version, required=True))
        model_count: Optional[int] = None
        models: list[dict] = []
        models_verified = False
        try:
            models = await client.models()
            models_verified = True
            model_count = len(models)
            if model_count > 0:
                provider_capabilities.append(_capability_status("models", "ok", str(model_count), required=True))
            else:
                warnings.append("models_empty")
                provider_capabilities.append(_capability_status("models", "failed", "empty", required=True))
        except LemonadeClientError as exc:
            warnings.append(_external_lemonade_warning("models", exc))
            provider_capabilities.append(_capability_status("models", "failed", exc.kind, required=True))

        try:
            await client.stats()
            provider_capabilities.append(_capability_status("stats", "ok"))
        except LemonadeClientError as exc:
            status = "unsupported" if exc.kind == "not_found" else "failed"
            provider_capabilities.append(_capability_status("stats", status, exc.kind))
            if status == "failed":
                warnings.append(_external_lemonade_warning("stats", exc))

        probe_model = _select_lemonade_probe_model(env_get, models, loaded_model)
        canonical_model = _model_env(env_get, "LEMONADE_MODEL")
        legacy_configured_model = _model_env(env_get, "LLM_MODEL")
        if not canonical_model and legacy_configured_model:
            legacy_model = _legacy_lemonade_chat_model(env_get, models)
            if legacy_model and probe_model == legacy_model:
                warnings.append("chat_model_legacy_llm_model")
            else:
                warnings.append("chat_model_legacy_llm_model_ignored")

        if active:
            await _probe_selected_lemonade_capabilities(
                client,
                provider_capabilities,
                warnings,
                env_get,
                api_base,
                api_path,
                probe_model,
                client_cls,
            )
            # Run direct chat last so probes that load specialized models do not
            # leave the provider switched away from Dream's primary chat model.
            if probe_model:
                try:
                    completion = await client.chat_completion(
                        probe_model,
                        [{"role": "user", "content": "ping"}],
                        max_tokens=1,
                        stream=False,
                        extra_body={"temperature": 0},
                    )
                    if _chat_completion_ready(completion):
                        provider_capabilities.append(_capability_status("chat", "ok", probe_model, required=True))
                    else:
                        warnings.append("chat_invalid_response")
                        provider_capabilities.append(
                            _capability_status("chat", "failed", "invalid_response", required=True)
                        )
                except LemonadeClientError as exc:
                    warnings.append(_external_lemonade_warning("chat", exc))
                    provider_capabilities.append(_capability_status("chat", "failed", exc.kind, required=True))
            else:
                warnings.append("chat_model_missing")
                provider_capabilities.append(_capability_status("chat", "failed", "model_missing", required=True))

            final_health = "reachable"
            try:
                refreshed_health = await client.health()
                refreshed_status = _health_status(refreshed_health)
                if refreshed_status in HEALTH_OK_STATUSES:
                    version = str(refreshed_health.get("version") or version)
                    if "all_models_loaded" in refreshed_health or any(
                        key in refreshed_health
                        for key in ("model_loaded", "loaded_model", "active_model", "model")
                    ):
                        loaded_model = _loaded_model_from_health(refreshed_health)
                        loaded_models = _loaded_models_from_health(refreshed_health)
                else:
                    warnings.append("health_refresh_unhealthy")
                    final_health = "unhealthy"
                    provider_capabilities.append(_capability_status("health_refresh", "failed", refreshed_status))
            except LemonadeClientError as exc:
                warnings.append(_external_lemonade_warning("health_refresh", exc))
                final_health = "unreachable" if exc.kind in {"provider_unreachable", "timeout"} else "unhealthy"
                provider_capabilities.append(_capability_status("health_refresh", "failed", exc.kind))

            try:
                refreshed_models = await client.models()
                models = refreshed_models
                model_count = len(refreshed_models)
                if model_count > 0:
                    _replace_capability(provider_capabilities, "models", "ok", str(model_count), required=True)
                else:
                    warnings.append("models_refresh_empty")
                    _replace_capability(provider_capabilities, "models", "failed", "empty", required=True)
            except LemonadeClientError as exc:
                warnings.append(_external_lemonade_warning("models_refresh", exc))
                model_count = None
                provider_capabilities.append(_capability_status("models_refresh", "failed", exc.kind))
        else:
            final_health = "reachable"
            provider_capabilities.append(
                _passive_model_capability(
                    "chat",
                    probe_model,
                    models,
                    loaded_models,
                    warnings,
                    models_verified=models_verified,
                )
            )
            _describe_selected_lemonade_capabilities(
                provider_capabilities,
                warnings,
                env_get,
                api_base,
                api_path,
                probe_model,
                models,
                loaded_models,
                models_verified,
            )

    return ExternalLemonadeProbeResult(
        final_health,
        version,
        warnings,
        loaded_model,
        model_count,
        provider_capabilities,
        probe_mode,
        loaded_models,
    )
