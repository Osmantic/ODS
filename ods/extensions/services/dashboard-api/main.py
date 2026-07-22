#!/usr/bin/env python3
"""
ODS Dashboard API
Lightweight backend providing system status for the Dashboard UI.

Default port: DASHBOARD_API_PORT (3002)

Modules:
  config.py       — Shared configuration and manifest loading
  models.py       — Pydantic response schemas
  security.py     — API key authentication
  gpu.py          — GPU detection (NVIDIA + AMD)
  helpers.py      — Service health, LLM metrics, system metrics
  routers/        — Endpoint modules (workflows, features, setup, updates, agents, privacy)
"""

import asyncio
import json
import logging
import os
import re
import socket
import shutil
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Depends, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware

# --- Local modules ---
from config import (
    SERVICES, DATA_DIR, INSTALL_DIR, SIDEBAR_ICONS, MANIFEST_ERRORS,
    AGENT_HOST, AGENT_PORT, AGENT_URL, ODS_AGENT_KEY,
    _detect_container_default_gateway, _running_inside_container,
    _read_env_from_file,
)
from models import (
    GPUInfo, ServiceStatus, DiskUsage, ModelInfo, BootstrapStatus,
    FullStatus, PortCheckRequest,
)
from security import verify_api_key
from gpu import get_gpu_info
from helpers import (
    get_all_services, get_cached_services, set_services_cache,
    get_disk_usage, dir_size_gb, get_model_info, get_bootstrap_status,
    get_uptime, get_cpu_metrics, get_ram_metrics,
    get_llama_metrics, get_loaded_model, get_llama_context_size,
    _get_httpx_client,
)
from context_policy import HERMES_MIN_CONTEXT, HERMES_TARGET_CONTEXT
from host_agent_client import (
    AgentHTTPError,
    AgentProtocolError,
    AgentUnavailable,
    request_json as request_agent_json,
    shutdown_clients as shutdown_agent_clients,
)
from agent_monitor import collect_metrics
from routers import (
    workflows, features, setup, updates, agents, privacy, extensions,
    gpu as gpu_router, resources, voice, models as models_router, model_state as model_state_router,
    model_routes as model_routes_router, templates,
    auth as auth_router,
    magic_link,
    oauth_passthrough,
    talk,
    tailscale,
    usage,
)
from settings import (
    _ENV_ASSIGNMENT_RE, _ENV_COMMENTED_ASSIGNMENT_RE, _SETTINGS_APPLY_ALLOWED_SERVICES, _parse_env_text, _read_env_map_from_path,
    _slugify,
    _build_env_fields, _validate_env_values, _serialize_form_values,
    _empty_value_unsets_env_key,
    _compute_env_apply_plan,
    _check_host_agent_available,
)


# ================================================================
# TTL Cache — avoids redundant subprocess/IO calls every poll cycle
# ================================================================

_CACHE_MISS = object()


class TTLCache:
    """Simple in-memory cache with per-key TTL (seconds)."""

    def __init__(self):
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str, default: object | None = None) -> object | None:
        entry = self._store.get(key)
        if entry is None:
            return default
        expires_at, value = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return default
        return value

    def set(self, key: str, value: object, ttl: float):
        self._store[key] = (time.monotonic() + ttl, value)

    def invalidate(self, key: str) -> None:
        """Remove a single cache entry."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Remove all cache entries."""
        self._store.clear()


_cache = TTLCache()

# Cache TTLs (seconds)
_GPU_CACHE_TTL = 3.0
_STATUS_CACHE_TTL = 2.0
_STORAGE_CACHE_TTL = 30.0
_SETTINGS_SUMMARY_CACHE_TTL = 5.0
_SETTINGS_CONFIG_CACHE_TTL = 15.0
_SETTINGS_ENV_CACHE_TTL = 5.0
_SERVICE_POLL_INTERVAL = 10.0  # background health check interval
_host_agent_probe_state: dict[str, Optional[str]] = {
    "last_success_at": None,
    "last_error": None,
}

logger = logging.getLogger(__name__)


def _resolve_install_root() -> Path:
    host_root = Path("/ods")
    if host_root.exists():
        return host_root
    return Path(INSTALL_DIR)


def _read_installed_version() -> str:
    install_root = _resolve_install_root()
    env_file = install_root / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text().splitlines():
                if line.startswith("ODS_VERSION="):
                    return line.split("=", 1)[1].strip().strip("\"'")
        except OSError:
            pass

    version_file = install_root / ".version"
    if version_file.exists():
        try:
            raw = version_file.read_text().strip()
            if raw:
                if raw.startswith("{"):
                    data = json.loads(raw)
                    if isinstance(data, dict) and data.get("version"):
                        return str(data["version"])
                return raw
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    manifest_file = install_root / "manifest.json"
    if manifest_file.exists():
        try:
            data = json.loads(manifest_file.read_text())
            version = (
                data.get("release", {}).get("version")
                or data.get("ods_version")
                or data.get("manifestVersion")
            )
            if version:
                return str(version)
        except (OSError, json.JSONDecodeError, ValueError, AttributeError):
            pass

    return app.version


def _probe_host_agent_health() -> dict[str, Any]:
    """Probe the host-agent health endpoint and update diagnostic state."""
    started = time.monotonic()
    try:
        body: Any = request_agent_json("GET", "/health", timeout=3)
        status_code = 200
        latency_ms = round((time.monotonic() - started) * 1000)
        success_at = datetime.now(timezone.utc).isoformat()
        _host_agent_probe_state["last_success_at"] = success_at
        _host_agent_probe_state["last_error"] = None
        return {
            "available": status_code == 200,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "response": body,
            "error": None,
        }
    except AgentHTTPError as exc:
        latency_ms = round((time.monotonic() - started) * 1000)
        status_code = exc.status_code
        detail = f"HTTP {exc.status_code}: {exc.detail}"
    except (AgentUnavailable, AgentProtocolError) as exc:
        latency_ms = round((time.monotonic() - started) * 1000)
        status_code = None
        detail = str(exc)

    _host_agent_probe_state["last_error"] = detail
    return {
        "available": False,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "response": None,
        "error": detail,
    }


def _normalize_timestamp_precision(timestamp: str) -> str:
    match = re.match(r"^(.*?\.\d{6})\d+(.*)$", timestamp)
    if match:
        return f"{match.group(1)}{match.group(2)}"
    return timestamp


def _service_by_id(statuses: list[ServiceStatus], service_id: str) -> Optional[ServiceStatus]:
    for service in statuses:
        if service.id == service_id:
            return service
    return None


def _service_is_healthy(statuses: list[ServiceStatus], service_id: str) -> bool:
    service = _service_by_id(statuses, service_id)
    return bool(service and service.status == "healthy")


def _readiness_check(
    *,
    check_id: str,
    name: str,
    required: bool,
    ready: bool,
    status: str,
    detail: str,
    repair: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": check_id,
        "name": name,
        "required": required,
        "ready": ready,
        "status": status,
        "detail": detail,
    }
    if repair:
        payload["repair"] = repair
    return payload


def _build_readiness_payload(
    *,
    service_statuses: list[ServiceStatus],
    loaded_model: Optional[str],
    context_size: Optional[int],
    bootstrap_info: BootstrapStatus,
    host_agent: dict[str, Any],
    stt_model_cached: Optional[bool],
    stt_model_name: str,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    llama_healthy = _service_is_healthy(service_statuses, "llama-server")
    chat_ready = bool(llama_healthy and loaded_model and context_size)
    if chat_ready:
        chat_detail = f"{loaded_model} loaded with {context_size} context"
    elif bootstrap_info.active:
        chat_detail = "Full model is still downloading; bootstrap mode may be limited"
    elif not llama_healthy:
        chat_detail = "llama-server is not healthy"
    elif not loaded_model:
        chat_detail = "No loaded model reported by the inference server"
    else:
        chat_detail = "Inference context size is unavailable"
    checks.append(_readiness_check(
        check_id="chat",
        name="Chat",
        required=True,
        ready=chat_ready,
        status="ready" if chat_ready else "blocked",
        detail=chat_detail,
        repair="ods restart llama-server" if not chat_ready and not bootstrap_info.active else None,
    ))

    webui_ready = _service_is_healthy(service_statuses, "open-webui")
    checks.append(_readiness_check(
        check_id="open-webui",
        name="Open WebUI",
        required=True,
        ready=webui_ready,
        status="ready" if webui_ready else "blocked",
        detail="Open WebUI is reachable" if webui_ready else "Open WebUI is not healthy",
        repair="ods restart open-webui" if not webui_ready else None,
    ))

    checks.append(_readiness_check(
        check_id="dashboard-api",
        name="Dashboard API",
        required=True,
        ready=True,
        status="ready",
        detail="Dashboard API is serving this readiness response",
    ))

    host_agent_ready = bool(host_agent.get("available"))
    checks.append(_readiness_check(
        check_id="host-agent",
        name="Host Agent",
        required=False,
        ready=host_agent_ready,
        status="ready" if host_agent_ready else "needs_repair",
        detail="Host agent is reachable" if host_agent_ready else "Host agent is not reachable",
        repair="ods agent restart" if not host_agent_ready else None,
    ))

    hermes_service = _service_by_id(service_statuses, "hermes")
    if hermes_service is None or hermes_service.status == "not_deployed":
        checks.append(_readiness_check(
            check_id="hermes",
            name="Hermes",
            required=False,
            ready=False,
            status="disabled",
            detail="Hermes is not enabled in this stack",
        ))
    else:
        hermes_ready = hermes_service.status == "healthy"
        checks.append(_readiness_check(
            check_id="hermes",
            name="Hermes",
            required=False,
            ready=hermes_ready,
            status="ready" if hermes_ready else "needs_repair",
            detail="Hermes is reachable" if hermes_ready else f"Hermes status is {hermes_service.status}",
            repair="ods restart hermes" if not hermes_ready else None,
        ))

    whisper = _service_by_id(service_statuses, "whisper")
    tts = _service_by_id(service_statuses, "tts")
    voice_enabled = bool(whisper or tts)
    if not voice_enabled:
        checks.append(_readiness_check(
            check_id="voice",
            name="Voice",
            required=False,
            ready=False,
            status="disabled",
            detail="Voice services are not enabled in this stack",
        ))
        can_use_voice = False
    else:
        whisper_ready = bool(whisper and whisper.status == "healthy")
        tts_ready = bool(tts and tts.status == "healthy")
        model_ready = stt_model_cached is True
        can_use_voice = whisper_ready and tts_ready and model_ready
        if can_use_voice:
            voice_detail = f"Whisper model {stt_model_name} cached and TTS is healthy"
        elif not whisper_ready:
            voice_detail = "Whisper STT is not healthy"
        elif not model_ready:
            voice_detail = f"Whisper STT model {stt_model_name} is not cached"
        else:
            voice_detail = "Kokoro TTS is not healthy"
        checks.append(_readiness_check(
            check_id="voice",
            name="Voice",
            required=False,
            ready=can_use_voice,
            status="ready" if can_use_voice else "needs_repair",
            detail=voice_detail,
            repair="ods repair voice" if not can_use_voice else None,
        ))

    required_ready = all(check["ready"] for check in checks if check["required"])
    optional_issues = [
        check for check in checks
        if not check["required"] and check["status"] not in {"ready", "disabled"}
    ]
    status = "ready" if required_ready and not optional_issues else ("degraded" if required_ready else "blocked")
    repair_hints = [check["repair"] for check in checks if check.get("repair")]

    return {
        "ready": required_ready,
        "status": status,
        "canChat": chat_ready,
        "canUseVoice": can_use_voice,
        "checks": checks,
        "issues": [check for check in checks if not check["ready"] and check["status"] != "disabled"],
        "repairHints": repair_hints,
    }


async def _check_stt_model_cached() -> tuple[Optional[bool], str]:
    model_name = os.environ.get("AUDIO_STT_MODEL") or _read_env_from_file("AUDIO_STT_MODEL") or "Systran/faster-whisper-base"
    whisper_cfg = SERVICES.get("whisper")
    if not whisper_cfg:
        return None, model_name

    host = whisper_cfg.get("host", "localhost")
    port = whisper_cfg.get("port", 8000)
    encoded = model_name.replace("/", "%2F")
    try:
        client = await _get_httpx_client()
        resp = await client.get(f"http://{host}:{port}/v1/models/{encoded}")
        return resp.status_code == 200, model_name
    except (httpx.HTTPError, httpx.TimeoutException, OSError):
        return False, model_name


def _read_install_date() -> Optional[str]:
    install_root = _resolve_install_root()
    env_file = install_root / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines()[:8]:
                if line.startswith("# Generated by ") and " on " in line:
                    raw_timestamp = line.split(" on ", 1)[1].strip()
                    normalized = _normalize_timestamp_precision(raw_timestamp)
                    try:
                        return datetime.fromisoformat(normalized).isoformat()
                    except ValueError:
                        return raw_timestamp
        except OSError:
            pass

    for candidate in (
        env_file,
        install_root / ".version",
        install_root / "manifest.json",
    ):
        if candidate.exists():
            try:
                return datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc).isoformat()
            except OSError:
                continue

    return None


def _infer_tier(gpu_info) -> str:
    if not gpu_info:
        return "Unknown"

    vram_gb = gpu_info.memory_total_mb / 1024
    if gpu_info.memory_type == "unified" and gpu_info.gpu_backend == "amd":
        return "Strix Halo 90+" if vram_gb >= 90 else "Strix Halo Compact"
    if vram_gb >= 80:
        return "Professional"
    if vram_gb >= 24:
        return "Prosumer"
    if vram_gb >= 16:
        return "Standard"
    if vram_gb >= 8:
        return "Entry"
    return "Minimal"


def _infer_gpu_count(gpu_info) -> int:
    """Infer GPU count from the GPU_COUNT env var or the display name."""
    observed_count = int(getattr(gpu_info, "gpu_count", 1) or 1)
    if observed_count > 1:
        return observed_count
    gpu_count_env = os.environ.get("GPU_COUNT", "")
    if gpu_count_env.isdigit():
        return int(gpu_count_env)
    if " × " in gpu_info.name:
        try:
            return int(gpu_info.name.rsplit(" × ", 1)[-1])
        except ValueError:
            pass
    if " + " in gpu_info.name:
        return gpu_info.name.count(" + ") + 1
    return 1


def _serialize_gpu(gpu_info) -> Optional[dict]:
    if not gpu_info:
        return None

    gpu_count = _infer_gpu_count(gpu_info)

    gpu_data = {
        "name": gpu_info.name,
        "vramUsed": (
            round(gpu_info.memory_used_mb / 1024, 1)
            if gpu_info.memory_usage_available else None
        ),
        "vramTotal": round(gpu_info.memory_total_mb / 1024, 1),
        "utilization": gpu_info.utilization_percent if gpu_info.utilization_available else None,
        "temperature": gpu_info.temperature_c if gpu_info.temperature_available else None,
        "memoryType": gpu_info.memory_type,
        "backend": gpu_info.gpu_backend,
        "gpu_count": gpu_count,
        "memoryLabel": "VRAM Partition" if gpu_info.memory_type == "unified" else "VRAM",
    }
    if gpu_info.power_w is not None:
        gpu_data["powerDraw"] = gpu_info.power_w
    return gpu_data


def _serialize_model(model_info) -> Optional[dict]:
    if not model_info:
        return None
    return {
        "name": model_info.name,
        "contextLength": model_info.context_length,
    }


def _build_model_readiness_payload(
    *,
    model_info: Optional[ModelInfo],
    bootstrap_info: BootstrapStatus,
    loaded_model: Optional[str],
    runtime_context: Optional[int],
) -> dict[str, Any]:
    configured_context = model_info.context_length if model_info else None
    effective_context = runtime_context or configured_context
    meets_hermes_minimum = bool(effective_context and effective_context >= HERMES_MIN_CONTEXT)
    meets_hermes_target = bool(effective_context and effective_context >= HERMES_TARGET_CONTEXT)
    has_loaded_model = bool(loaded_model)
    ready = has_loaded_model and meets_hermes_minimum

    issues: list[str] = []
    if not has_loaded_model:
        issues.append("No model is currently reported as loaded.")
    if not meets_hermes_minimum:
        issues.append(f"Context is below Hermes minimum ({HERMES_MIN_CONTEXT}).")
    if bootstrap_info.active:
        issues.append("Full model is still downloading; bootstrap model is serving first-run traffic.")

    if ready and bootstrap_info.active:
        status = "bootstrap"
    elif ready:
        status = "ready"
    else:
        status = "blocked"

    return {
        "ready": ready,
        "status": status,
        "activeModel": loaded_model,
        "configuredModel": {
            "name": model_info.name,
            "contextLength": configured_context,
            "quantization": model_info.quantization,
            "sizeGb": model_info.size_gb,
        } if model_info else None,
        "bootstrap": {
            "active": bootstrap_info.active,
            "model": bootstrap_info.model_name,
            "percent": bootstrap_info.percent,
            "downloadedGb": bootstrap_info.downloaded_gb,
            "totalGb": bootstrap_info.total_gb,
            "etaSeconds": bootstrap_info.eta_seconds,
        },
        "context": {
            "configured": configured_context,
            "runtime": runtime_context,
            "effective": effective_context,
            "hermesMinimum": HERMES_MIN_CONTEXT,
            "hermesTarget": HERMES_TARGET_CONTEXT,
            "meetsHermesMinimum": meets_hermes_minimum,
            "meetsHermesTarget": meets_hermes_target,
        },
        "hermes": {
            "compatible": meets_hermes_minimum,
            "targetReady": meets_hermes_target,
            "minimumContext": HERMES_MIN_CONTEXT,
            "targetContext": HERMES_TARGET_CONTEXT,
        },
        "issues": issues,
    }


def _service_semantics(service_id: str, status: str) -> dict:
    config = SERVICES.get(service_id, {})
    category = config.get("category", "optional")
    required = category == "core"

    if status == "healthy":
        state = "ready"
        severity = "ok"
        counts_as_issue = False
    elif status == "not_deployed":
        state = "disabled"
        severity = "disabled"
        counts_as_issue = False
    elif status == "unknown" and not required:
        state = "unknown"
        severity = "unknown"
        counts_as_issue = False
    elif required:
        state = "blocked"
        severity = "critical"
        counts_as_issue = True
    else:
        state = "attention"
        severity = "warning"
        counts_as_issue = True

    return {
        "category": category,
        "required": required,
        "impact": "core" if required else "optional",
        "state": state,
        "severity": severity,
        "countsAsIssue": counts_as_issue,
    }


def _service_public_url(service_id: str, port: int | None) -> Optional[str]:
    if not port:
        return None
    config = SERVICES.get(service_id, {})
    path = str(config.get("ui_path") or "/").strip() or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    return f"http://127.0.0.1:{port}{path}"




def _serialize_services(service_statuses: list[ServiceStatus], uptime: int) -> list[dict]:
    serialized = []
    for service in service_statuses:
        url = _service_public_url(service.id, service.external_port)
        item = {
            "id": service.id,
            "name": service.name,
            "status": service.status,
            "port": service.external_port,
            "uptime": uptime if service.status == "healthy" else None,
        }
        if url:
            item["url"] = url
            item["href"] = url
        llm_contract = SERVICES.get(service.id, {}).get("llm")
        if isinstance(llm_contract, dict):
            item["llm"] = llm_contract
        item.update(_service_semantics(service.id, service.status))
        serialized.append(item)
    return serialized


def _fallback_services() -> list[dict]:
    links = []
    for service_id, config in SERVICES.items():
        external_port = config.get("external_port", config.get("port", 0))
        if not external_port:
            continue
        url = _service_public_url(service_id, external_port)
        item = {
            "id": service_id,
            "name": config.get("name", service_id),
            "status": "unknown",
            "port": external_port,
            "uptime": None,
        }
        if url:
            item["url"] = url
            item["href"] = url
        llm_contract = config.get("llm")
        if isinstance(llm_contract, dict):
            item["llm"] = llm_contract
        item.update(_service_semantics(service_id, "unknown"))
        links.append(item)
    return links


def _resolve_runtime_env_path() -> Path:
    install_root = _resolve_install_root()
    env_path = install_root / ".env"
    if env_path.exists():
        return env_path
    return Path(INSTALL_DIR) / ".env"


def _resolve_bundled_path(name: str) -> Path:
    return Path(__file__).resolve().parent / name


def _resolve_template_path(name: str) -> Path:
    install_root = _resolve_install_root()
    for candidate in (
        install_root / name,
        _resolve_bundled_path(name),
        Path(INSTALL_DIR) / name,
    ):
        if candidate.exists():
            return candidate
    return _resolve_bundled_path(name)


def _load_env_schema() -> tuple[dict[str, Any], set[str]]:
    schema_path = _resolve_template_path(".env.schema.json")
    if not schema_path.exists():
        return {}, set()

    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}, set()

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    if not isinstance(properties, dict):
        properties = {}
    return properties, required


def _build_env_sections(schema_keys: list[str]) -> list[dict[str, Any]]:
    example_path = _resolve_template_path(".env.example")
    if not example_path.exists():
        return [{
            "id": "configuration",
            "title": "Configuration",
            "keys": schema_keys,
        }]

    try:
        lines = example_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [{
            "id": "configuration",
            "title": "Configuration",
            "keys": schema_keys,
        }]

    sections: list[dict[str, Any]] = []
    section_index: dict[str, dict[str, Any]] = {}
    current = {"id": "configuration", "title": "Configuration", "keys": []}
    sections.append(current)
    section_index[current["id"]] = current

    def ensure_section(title: str) -> dict[str, Any]:
        slug = _slugify(title) or "configuration"
        if slug in section_index:
            return section_index[slug]
        section = {"id": slug, "title": title, "keys": []}
        sections.append(section)
        section_index[slug] = section
        return section

    idx = 0
    while idx < len(lines):
        if (
            idx + 2 < len(lines)
            and lines[idx].lstrip().startswith("#")
            and set(lines[idx].replace("#", "").strip()) <= {"═"}
            and lines[idx + 1].lstrip().startswith("#")
            and set(lines[idx + 2].replace("#", "").strip()) <= {"═"}
        ):
            title = lines[idx + 1].lstrip("#").strip()
            if title:
                current = ensure_section(title)
            idx += 3
            continue

        match = _ENV_ASSIGNMENT_RE.match(lines[idx]) or _ENV_COMMENTED_ASSIGNMENT_RE.match(lines[idx])
        if match:
            key = match.group(1)
            if key in schema_keys and key not in current["keys"]:
                current["keys"].append(key)
        idx += 1

    remaining = [key for key in schema_keys if not any(key in section["keys"] for section in sections)]
    if remaining:
        extra = ensure_section("Advanced")
        extra["keys"].extend(remaining)

    return [section for section in sections if section["keys"]]


def _render_env_from_values(values: dict[str, str]) -> str:
    example_path = _resolve_template_path(".env.example")
    seen: set[str] = set()
    output_lines: list[str] = []

    if example_path.exists():
        try:
            example_lines = example_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            example_lines = []
    else:
        example_lines = []

    for line in example_lines:
        assignment = _ENV_ASSIGNMENT_RE.match(line)
        commented_assignment = _ENV_COMMENTED_ASSIGNMENT_RE.match(line)

        if assignment:
            key = assignment.group(1)
            output_lines.append(f"{key}={values.get(key, '')}")
            seen.add(key)
            continue

        if commented_assignment:
            key = commented_assignment.group(1)
            seen.add(key)
            if key in values:
                output_lines.append(f"{key}={values[key]}")
            else:
                output_lines.append(line)
            continue

        output_lines.append(line)

    extras = [(key, value) for key, value in values.items() if key not in seen]
    if extras:
        if output_lines and output_lines[-1] != "":
            output_lines.append("")
        output_lines.extend([
            "# Additional Local Overrides",
            "# Values below were preserved because they are not part of .env.example.",
        ])
        for key, value in extras:
            output_lines.append(f"{key}={value}")

    return "\n".join(output_lines).rstrip() + "\n"


def _clear_settings_caches():
    for key in ("settings_summary", "settings_env", "status"):
        _cache.invalidate(key)


def _call_agent_core_recreate(service_ids: list[str]) -> dict[str, Any]:
    return request_agent_json(
        "POST",
        "/v1/core/recreate",
        payload={"service_ids": service_ids},
        timeout=180,
    )


def _call_agent_env_update(raw_text: str) -> dict[str, Any]:
    """Route .env writes through the host agent (filesystem is :ro in container)."""
    return request_agent_json(
        "POST",
        "/v1/env/update",
        payload={"raw_text": raw_text, "backup": True},
        timeout=60,
    )


def _build_settings_env_payload(
    *,
    raw_text: Optional[str] = None,
    backup_path: Optional[str] = None,
    apply_plan: Optional[dict[str, Any]] = None,
) -> dict:
    env_path = _resolve_runtime_env_path()
    if raw_text is None:
        try:
            raw_text = env_path.read_text(encoding="utf-8")
        except OSError:
            raw_text = ""

    values, parse_issues = _parse_env_text(raw_text)
    schema_properties, required_keys = _load_env_schema()
    fields = _build_env_fields(schema_properties, required_keys, values)
    sections = _build_env_sections(list(fields.keys()))
    issues = _validate_env_values(values, fields, parse_issues)
    public_fields: dict[str, dict[str, Any]] = {}
    public_values: dict[str, str] = {}

    for key, field in fields.items():
        public_field = {**field}
        if field.get("secret"):
            public_field["value"] = ""
            public_values[key] = ""
        else:
            public_values[key] = field["value"]
        public_fields[key] = public_field

    return {
        "path": _relative_install_path(env_path),
        "raw": "",
        "values": public_values,
        "fields": public_fields,
        "sections": sections,
        "issues": issues,
        "saveHint": "Saving writes the .env file directly, keeps existing secret values when left blank, never sends stored secrets back to the browser, and stores a timestamped backup under data/config-backups first.",
        "restartHint": "Some ODS services need a container recreate before changed values fully take effect. Use Apply changes when it becomes available after saving.",
        "backupPath": backup_path,
        "applyPlan": apply_plan,
        "agentAvailable": _check_host_agent_available(),
    }


def _relative_install_path(path: Path) -> str:
    try:
        return str(path.relative_to(_resolve_install_root())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _prepare_env_save(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    mode = payload.get("mode", "form")
    env_path = _resolve_runtime_env_path()
    current_values, _ = _read_env_map_from_path(env_path)
    schema_properties, required_keys = _load_env_schema()

    if mode != "form":
        raise HTTPException(
            status_code=400,
            detail={"message": "Only form-based editing is supported for security reasons."},
        )

    submitted_values = payload.get("values", {})
    if not isinstance(submitted_values, dict):
        raise HTTPException(
            status_code=400,
            detail={"message": "Form configuration payload must be an object."},
        )

    base_fields = _build_env_fields(schema_properties, required_keys, current_values)
    invalid_keys = sorted(set(submitted_values.keys()) - set(base_fields.keys()))
    if invalid_keys:
        return _render_env_from_values(current_values), [
            {
                "key": key,
                "message": "Field is not editable from the dashboard. Only schema-backed fields and existing local overrides can be changed here.",
            }
            for key in invalid_keys
        ], _compute_env_apply_plan(current_values, current_values)

    read_only_changes = []
    for key, submitted_value in submitted_values.items():
        field = base_fields[key]
        if not field.get("readOnly"):
            continue
        current_value = current_values.get(key, "")
        if str(submitted_value) != current_value:
            read_only_changes.append({
                "key": key,
                "message": field.get("readOnlyReason") or "Field is read-only.",
            })
    if read_only_changes:
        return (
            _render_env_from_values(current_values),
            read_only_changes,
            _compute_env_apply_plan(current_values, current_values),
        )

    normalized_values = _serialize_form_values(submitted_values, base_fields, current_values)
    merged_values = {**current_values, **normalized_values}
    for key, field in base_fields.items():
        if _empty_value_unsets_env_key(key, field) and str(merged_values.get(key, "")).strip() == "":
            merged_values.pop(key, None)
    merged_fields = _build_env_fields(schema_properties, required_keys, merged_values)
    issues = _validate_env_values(merged_values, merged_fields)
    apply_plan = _compute_env_apply_plan(current_values, merged_values)
    return _render_env_from_values(merged_values), issues, apply_plan

# --- App ---

@asynccontextmanager
async def _lifespan(app: FastAPI):
    background_tasks = [
        asyncio.create_task(collect_metrics()),
        asyncio.create_task(_poll_service_health()),
        asyncio.create_task(gpu_router.poll_gpu_history()),
    ]
    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        # Close any open Hermes WebSockets in the ODS Talk connection pool
        # so a graceful uvicorn shutdown doesn't leak FDs into stale state.
        try:
            import hermes_bridge
            await hermes_bridge.shutdown_pool()
        except Exception:
            logger.debug("hermes_bridge.shutdown_pool raised at app shutdown", exc_info=True)
        await shutdown_agent_clients()


app = FastAPI(
    title="ODS Dashboard API",
    version="2.5.3",
    description="System status API for ODS Dashboard",
    lifespan=_lifespan,
)

# --- CORS ---

def get_allowed_origins():
    env_origins = os.environ.get("DASHBOARD_ALLOWED_ORIGINS", "")
    if env_origins:
        return env_origins.split(",")
    origins = [
        "http://localhost:3001", "http://127.0.0.1:3001",
        "http://localhost:3000", "http://127.0.0.1:3000",
    ]
    try:
        hostname = socket.gethostname()
        local_ips = socket.gethostbyname_ex(hostname)[2]
        for ip in local_ips:
            if ip.startswith(("192.168.", "10.", "172.")):
                origins.append(f"http://{ip}:3001")
                origins.append(f"http://{ip}:3000")
    except (OSError, socket.gaierror):
        logger.debug("Could not detect LAN IPs for CORS origins")
    return origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)

# --- Include Routers ---

app.include_router(workflows.router)
app.include_router(features.router)
app.include_router(setup.router)
app.include_router(updates.router)
app.include_router(agents.router)
app.include_router(privacy.router)
app.include_router(extensions.router)
app.include_router(gpu_router.router)
app.include_router(resources.router)
app.include_router(voice.router)
# Static switchboard state route registers before the dynamic model-ID routes.
app.include_router(model_state_router.router)
app.include_router(model_routes_router.router)
app.include_router(models_router.router)
app.include_router(templates.router)
app.include_router(auth_router.router)
app.include_router(magic_link.router)
app.include_router(oauth_passthrough.router)
app.include_router(talk.router)
app.include_router(tailscale.router)
app.include_router(usage.router)


# ================================================================
# Core Endpoints (health, status, preflight, services)
# ================================================================

@app.get("/health")
async def health():
    """API health check."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/host-agent/diagnostics", dependencies=[Depends(verify_api_key)])
async def host_agent_diagnostics():
    """Report how dashboard-api resolves and reaches the host agent."""
    inside_container = await asyncio.to_thread(_running_inside_container)
    default_gateway = None
    if inside_container:
        default_gateway = await asyncio.to_thread(_detect_container_default_gateway)
    probe = await asyncio.to_thread(_probe_host_agent_health)
    probe["last_success_at"] = _host_agent_probe_state["last_success_at"]
    probe["last_error"] = _host_agent_probe_state["last_error"]

    return {
        "configured": {
            "url": AGENT_URL,
            "host": AGENT_HOST,
            "port": AGENT_PORT,
            "ods_agent_key_configured": bool(ODS_AGENT_KEY),
            "ods_agent_host_explicit": bool(os.environ.get("ODS_AGENT_HOST", "").strip()),
        },
        "container": {
            "inside_container": inside_container,
            "default_gateway": default_gateway or None,
        },
        "probe": probe,
    }


# --- Preflight ---

@app.get("/api/preflight/docker", dependencies=[Depends(verify_api_key)])
async def preflight_docker():
    """Check if Docker is available."""
    if os.path.exists("/.dockerenv"):
        return {"available": True, "version": "available (host)"}
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            parts = stdout.decode().strip().split()
            version = parts[2].rstrip(",") if len(parts) > 2 else "unknown"
            return {"available": True, "version": version}
        return {"available": False, "error": "Docker command failed"}
    except FileNotFoundError:
        return {"available": False, "error": "Docker not installed"}
    except asyncio.TimeoutError:
        return {"available": False, "error": "Docker check timed out"}
    except OSError:
        logger.exception("Docker preflight check failed")
        return {"available": False, "error": "Docker check failed"}


@app.get("/api/preflight/gpu", dependencies=[Depends(verify_api_key)])
async def preflight_gpu():
    """Check GPU availability."""
    gpu_info = await asyncio.to_thread(get_gpu_info)
    if gpu_info:
        vram_gb = round(gpu_info.memory_total_mb / 1024, 1)
        result = {"available": True, "name": gpu_info.name, "vram": vram_gb, "backend": gpu_info.gpu_backend, "memory_type": gpu_info.memory_type}
        if gpu_info.memory_type == "unified":
            result["memory_label"] = f"{vram_gb} GB Unified"
        return result

    gpu_backend = os.environ.get("GPU_BACKEND", "").lower()
    if gpu_backend == "amd":
        return {"available": False, "error": "AMD GPU not detected via sysfs. Check /dev/kfd and /dev/dri access."}
    return {"available": False, "error": "No GPU detected. Ensure NVIDIA drivers or AMD amdgpu driver is loaded."}


@app.get("/api/preflight/required-ports", dependencies=[Depends(verify_api_key)])
async def preflight_required_ports():
    """Return the list of deployed service names and ports for preflight checking.

    Gated like the sibling preflight endpoints (docker/gpu/ports/disk): the
    response enumerates which services are live and on which ports, so it must
    not be reachable unauthenticated.
    """
    # When health cache exists, filter out services not in the compose stack
    cached = get_cached_services()
    deployed = {s.id for s in cached if s.status != "not_deployed"} if cached else None

    ports = []
    for sid, cfg in SERVICES.items():
        if deployed is not None and sid not in deployed:
            continue
        ext_port = cfg.get("external_port", cfg.get("port", 0))
        if ext_port:
            ports.append({"port": ext_port, "service": cfg.get("name", sid)})
    return {"ports": ports}


@app.post("/api/preflight/ports", dependencies=[Depends(verify_api_key)])
async def preflight_ports(request: PortCheckRequest):
    """Check if required ports are available."""
    port_services = {}
    for sid, cfg in SERVICES.items():
        ext_port = cfg.get("external_port", cfg.get("port", 0))
        if ext_port:
            port_services[ext_port] = cfg.get("name", sid)

    conflicts = []
    for port in request.ports:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                sock.bind(("0.0.0.0", port))
        except socket.error:
            conflicts.append({"port": port, "service": port_services.get(port, "Unknown"), "in_use": True})
    return {"conflicts": conflicts, "available": len(conflicts) == 0}


@app.get("/api/preflight/disk", dependencies=[Depends(verify_api_key)])
async def preflight_disk():
    """Check available disk space."""
    try:
        check_path = DATA_DIR if os.path.exists(DATA_DIR) else Path.home()
        usage = shutil.disk_usage(check_path)
        return {"free": usage.free, "total": usage.total, "used": usage.used, "path": str(check_path)}
    except OSError:
        logger.exception("Disk preflight check failed")
        return {"error": "Disk check failed", "free": 0, "total": 0, "used": 0, "path": ""}


# --- Core Data ---

@app.get("/gpu", response_model=Optional[GPUInfo])
async def gpu(api_key: str = Depends(verify_api_key)):
    """Get GPU metrics (cached for a few seconds to avoid nvidia-smi spam)."""
    cached = _cache.get("gpu_info", _CACHE_MISS)
    if cached is not _CACHE_MISS:
        if not cached:
            raise HTTPException(status_code=503, detail="GPU not available")
        return cached
    info = await asyncio.to_thread(get_gpu_info)
    _cache.set("gpu_info", info, _GPU_CACHE_TTL)
    if not info:
        raise HTTPException(status_code=503, detail="GPU not available")
    return info


@app.get("/services", response_model=list[ServiceStatus])
async def services(api_key: str = Depends(verify_api_key)):
    """Get all service health statuses (from background poll cache)."""
    cached = get_cached_services()
    if cached is not None:
        return cached
    return await get_all_services()


@app.get("/disk", response_model=DiskUsage)
async def disk(api_key: str = Depends(verify_api_key)):
    return await asyncio.to_thread(get_disk_usage)


@app.get("/model", response_model=Optional[ModelInfo])
async def model(api_key: str = Depends(verify_api_key)):
    return await asyncio.to_thread(get_model_info)


@app.get("/bootstrap", response_model=BootstrapStatus)
async def bootstrap(api_key: str = Depends(verify_api_key)):
    return await asyncio.to_thread(get_bootstrap_status)


@app.get("/api/model-readiness")
async def api_model_readiness(api_key: str = Depends(verify_api_key)):
    """Return first-run model/context readiness for Hermes and chat."""
    model_info, bootstrap_info, loaded_model = await asyncio.gather(
        asyncio.to_thread(get_model_info),
        asyncio.to_thread(get_bootstrap_status),
        get_loaded_model(),
    )
    runtime_context = await get_llama_context_size(model_hint=loaded_model)
    return _build_model_readiness_payload(
        model_info=model_info,
        bootstrap_info=bootstrap_info,
        loaded_model=loaded_model,
        runtime_context=runtime_context,
    )


@app.get("/status", response_model=FullStatus)
async def status(api_key: str = Depends(verify_api_key)):
    """Get full system status. Runs sync helpers in thread pool concurrently."""
    service_statuses, gpu_info, disk_info, model_info, bootstrap_info, uptime = await asyncio.gather(
        _get_services(),
        asyncio.to_thread(get_gpu_info),
        asyncio.to_thread(get_disk_usage),
        asyncio.to_thread(get_model_info),
        asyncio.to_thread(get_bootstrap_status),
        asyncio.to_thread(get_uptime),
    )
    return FullStatus(
        timestamp=datetime.now(timezone.utc).isoformat(),
        gpu=gpu_info, services=service_statuses,
        disk=disk_info, model=model_info,
        bootstrap=bootstrap_info, uptime_seconds=uptime
    )


@app.get("/api/status")
async def api_status(api_key: str = Depends(verify_api_key)):
    """Dashboard-compatible status endpoint.

    Catches transient I/O failures from sub-calls (GPU, health checks,
    llama metrics …) and returns a safe fallback. Programming errors
    (AttributeError, KeyError, TypeError) propagate so they surface in
    tests instead of being masked.
    """
    try:
        return await _build_api_status()
    except (asyncio.TimeoutError, OSError):
        logger.exception("/api/status handler failed — returning safe fallback")
        return {
            "gpu": None, "services": [], "model": None,
            "bootstrap": None, "uptime": 0,
            "version": app.version, "tier": "Unknown",
            "cpu": {"percent": 0, "temp_c": None},
            "ram": {"used_gb": 0, "total_gb": 0, "percent": 0},
            "disk": {"used_gb": 0, "total_gb": 0, "percent": 0},
            "system": {"uptime": 0, "hostname": os.environ.get("HOSTNAME", "ods")},
            "inference": {"tokensPerSecond": 0, "lifetimeTokens": 0,
                          "tokenCountMode": "unavailable",
                          "loadedModel": None, "contextSize": None},
            "manifest_errors": MANIFEST_ERRORS,
        }


@app.get("/api/readiness")
async def api_readiness(api_key: str = Depends(verify_api_key)):
    """Return user-workflow readiness, not just container health."""
    (
        service_statuses,
        loaded_model,
        context_size,
        bootstrap_info,
        host_agent,
        stt_result,
    ) = await asyncio.gather(
        _get_services(),
        get_loaded_model(),
        get_llama_context_size(),
        asyncio.to_thread(get_bootstrap_status),
        asyncio.to_thread(_probe_host_agent_health),
        _check_stt_model_cached(),
    )
    stt_model_cached, stt_model_name = stt_result
    return _build_readiness_payload(
        service_statuses=service_statuses,
        loaded_model=loaded_model,
        context_size=context_size,
        bootstrap_info=bootstrap_info,
        host_agent=host_agent,
        stt_model_cached=stt_model_cached,
        stt_model_name=stt_model_name,
    )


async def _build_api_status() -> dict:
    """Build the full status payload.

    Runs ALL sync helpers (GPU, disk, CPU, RAM, model, bootstrap)
    concurrently in the thread pool while async health checks and
    llama-server queries run on the event loop — no serial blocking.
    """
    # Fan out: sync helpers in threads + async health checks simultaneously
    (
        gpu_info, model_info, bootstrap_info, uptime,
        cpu_metrics, ram_metrics, disk_info,
        service_statuses, loaded_model,
    ) = await asyncio.gather(
        asyncio.to_thread(get_gpu_info),
        asyncio.to_thread(get_model_info),
        asyncio.to_thread(get_bootstrap_status),
        asyncio.to_thread(get_uptime),
        asyncio.to_thread(get_cpu_metrics),
        asyncio.to_thread(get_ram_metrics),
        asyncio.to_thread(get_disk_usage),
        _get_services(),
        get_loaded_model(),
    )

    # Second fan-out: llama metrics + context size (need loaded_model)
    llama_metrics_data, context_size = await asyncio.gather(
        get_llama_metrics(model_hint=loaded_model),
        get_llama_context_size(model_hint=loaded_model),
    )

    gpu_data = _serialize_gpu(gpu_info)

    services_data = _serialize_services(service_statuses, uptime)

    model_data = None
    if model_info:
        model_data = {
            "name": model_info.name,
            "currentModel": model_info.name,
            "configuredModel": model_info.name,
            "loadedModel": loaded_model or model_info.name,
            "tokensPerSecond": llama_metrics_data.get("tokens_per_second") or None,
            "contextLength": context_size or model_info.context_length,
        }

    bootstrap_data = None
    if bootstrap_info.active:
        bootstrap_data = {
            "active": True, "model": bootstrap_info.model_name or "Full Model",
            "percent": bootstrap_info.percent or 0,
            "bytesDownloaded": int((bootstrap_info.downloaded_gb or 0) * 1024**3),
            "bytesTotal": int((bootstrap_info.total_gb or 0) * 1024**3),
            "eta": bootstrap_info.eta_seconds, "speedMbps": bootstrap_info.speed_mbps
        }

    tier = _infer_tier(gpu_info)

    loaded_model_name = loaded_model or (model_data["name"] if model_data else None)
    configured_model_name = model_data["configuredModel"] if model_data else None

    result = {
        "gpu": gpu_data, "services": services_data, "model": model_data,
        "bootstrap": bootstrap_data, "uptime": uptime,
        "version": app.version, "tier": tier,
        "currentModel": configured_model_name,
        "loadedModel": loaded_model_name,
        "configuredModel": configured_model_name,
        "cpu": cpu_metrics, "ram": ram_metrics,
        "disk": {"used_gb": disk_info.used_gb, "total_gb": disk_info.total_gb, "percent": disk_info.percent},
        "system": {"uptime": uptime, "hostname": os.environ.get("HOSTNAME", "ods")},
        "inference": {
            "tokensPerSecond": llama_metrics_data.get("tokens_per_second", 0),
            "lifetimeTokens": llama_metrics_data.get("lifetime_tokens", 0),
            "tokenCountMode": llama_metrics_data.get("token_count_mode", "unavailable"),
            "loadedModel": loaded_model_name,
            "contextSize": context_size or (model_data["contextLength"] if model_data else None),
        },
        "manifest_errors": MANIFEST_ERRORS,
    }
    return result


# --- Settings ---

@app.get("/api/service-tokens", dependencies=[Depends(verify_api_key)])
async def service_tokens():
    """Return connection tokens for services that need browser-side auth."""
    def _read_tokens():
        tokens = {}
        oc_token = os.environ.get("OPENCLAW_TOKEN", "")
        if not oc_token:
            for path in [Path("/data/openclaw/home/gateway-token"), Path("/ods/.env")]:
                try:
                    if path.suffix == ".env":
                        for line in path.read_text().splitlines():
                            if line.startswith("OPENCLAW_TOKEN="):
                                oc_token = line.split("=", 1)[1].strip()
                                break
                    else:
                        oc_token = path.read_text().strip()
                except (OSError, ValueError):
                    continue
                if oc_token:
                    break
        if oc_token:
            tokens["openclaw"] = oc_token
        return tokens

    return await asyncio.to_thread(_read_tokens)


@app.get("/api/external-links")
async def get_external_links(api_key: str = Depends(verify_api_key)):
    """Return sidebar-ready external links derived from service manifests."""
    links = []
    for sid, cfg in SERVICES.items():
        ext_port = cfg.get("external_port", cfg.get("port", 0))
        if not ext_port or sid == "dashboard-api" or cfg.get("external_link") is False:
            continue
        links.append({
            "id": sid, "label": cfg.get("name", sid), "port": ext_port,
            "ui_path": cfg.get("ui_path", "/"),
            "icon": SIDEBAR_ICONS.get(sid, "ExternalLink"),
            "healthNeedles": [sid, cfg.get("name", sid).lower()],
        })
    return links


@app.get("/api/storage")
async def api_storage(api_key: str = Depends(verify_api_key)):
    """Get storage breakdown for Settings page (cached, runs in thread pool)."""
    cached = _cache.get("storage")
    if cached is not None:
        return cached

    def _compute_storage():
        models_dir = Path(DATA_DIR) / "models"
        vector_dir = Path(DATA_DIR) / "qdrant"
        data_dir = Path(DATA_DIR)

        disk_info = get_disk_usage()
        models_gb = dir_size_gb(models_dir)
        vector_gb = dir_size_gb(vector_dir)
        other_gb = dir_size_gb(data_dir) - models_gb - vector_gb
        total_data_gb = models_gb + vector_gb + max(other_gb, 0)

        return {
            "models": {"formatted": f"{models_gb:.1f} GB", "gb": models_gb, "percent": round(models_gb / disk_info.total_gb * 100, 1) if disk_info.total_gb else 0},
            "vector_db": {"formatted": f"{vector_gb:.1f} GB", "gb": vector_gb, "percent": round(vector_gb / disk_info.total_gb * 100, 1) if disk_info.total_gb else 0},
            "total_data": {"formatted": f"{total_data_gb:.1f} GB", "gb": total_data_gb, "percent": round(total_data_gb / disk_info.total_gb * 100, 1) if disk_info.total_gb else 0},
            "disk": {"used_gb": disk_info.used_gb, "total_gb": disk_info.total_gb, "percent": disk_info.percent}
        }

    result = await asyncio.to_thread(_compute_storage)
    _cache.set("storage", result, _STORAGE_CACHE_TTL)
    return result


@app.get("/api/settings/summary")
async def api_settings_summary(api_key: str = Depends(verify_api_key)):
    """Fast settings payload that avoids slow live service probes on first load."""
    cached = _cache.get("settings_summary")
    if cached is not None:
        return cached

    gpu_info, model_info, uptime, cpu_metrics, ram_metrics = await asyncio.gather(
        asyncio.to_thread(get_gpu_info),
        asyncio.to_thread(get_model_info),
        asyncio.to_thread(get_uptime),
        asyncio.to_thread(get_cpu_metrics),
        asyncio.to_thread(get_ram_metrics),
    )

    cached_services = get_cached_services()
    services_data = (
        _serialize_services(cached_services, uptime)
        if cached_services is not None
        else _fallback_services()
    )

    result = {
        "version": _read_installed_version(),
        "install_date": _read_install_date(),
        "tier": _infer_tier(gpu_info),
        "uptime": uptime,
        "cpu": cpu_metrics,
        "ram": ram_metrics,
        "gpu": _serialize_gpu(gpu_info),
        "model": _serialize_model(model_info),
        "services": services_data,
        "system": {
            "uptime": uptime,
            "hostname": os.environ.get("HOSTNAME", "ods"),
        },
        "manifest_errors": MANIFEST_ERRORS,
    }
    _cache.set("settings_summary", result, _SETTINGS_SUMMARY_CACHE_TTL)
    return result


@app.get("/api/settings/env")
async def api_settings_env(api_key: str = Depends(verify_api_key)):
    cached = _cache.get("settings_env")
    if cached is not None:
        return cached

    result = await asyncio.to_thread(_build_settings_env_payload)
    _cache.set("settings_env", result, _SETTINGS_ENV_CACHE_TTL)
    return result


@app.put("/api/settings/env")
async def api_settings_env_save(
    payload: dict[str, Any] = Body(...),
    api_key: str = Depends(verify_api_key),
):
    raw_text, issues, apply_plan = await asyncio.to_thread(_prepare_env_save, payload)
    if issues:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Configuration validation failed.",
                "issues": issues,
            },
        )

    try:
        agent_resp = await asyncio.to_thread(_call_agent_env_update, raw_text)
    except AgentHTTPError as exc:
        detail = exc.detail
        raise HTTPException(status_code=503, detail={"message": detail}) from exc
    except AgentUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": "ODS host agent is not reachable. Start the host agent, then try again."},
        ) from exc
    except AgentProtocolError as exc:
        logger.error("Failed to contact host agent for env update: %s", exc)
        raise HTTPException(
            status_code=500,
            detail={"message": "Could not contact host agent to write environment file."},
        ) from exc
    backup_relative = agent_resp.get("backup_path")

    _clear_settings_caches()
    result = await asyncio.to_thread(
        _build_settings_env_payload,
        raw_text=raw_text,
        backup_path=backup_relative,
        apply_plan=apply_plan,
    )
    _cache.set("settings_env", result, _SETTINGS_ENV_CACHE_TTL)
    return result


@app.post("/api/settings/env/apply")
async def api_settings_env_apply(
    payload: dict[str, Any] = Body(...),
    api_key: str = Depends(verify_api_key),
):
    service_ids = payload.get("service_ids", [])
    if not isinstance(service_ids, list) or not service_ids:
        raise HTTPException(
            status_code=400,
            detail={"message": "service_ids must be a non-empty list."},
        )

    normalized: list[str] = []
    for service_id in sorted(set(service_ids)):
        if not isinstance(service_id, str) or service_id not in _SETTINGS_APPLY_ALLOWED_SERVICES:
            raise HTTPException(
                status_code=400,
                detail={"message": f"Service is not eligible for dashboard-triggered apply: {service_id}"},
            )
        normalized.append(service_id)

    try:
        await asyncio.to_thread(_call_agent_core_recreate, normalized)
        _clear_settings_caches()
        return {
            "success": True,
            "services": normalized,
            "message": f"Applied runtime changes to {', '.join(normalized)}.",
        }
    except AgentHTTPError as exc:
        detail = exc.detail
        raise HTTPException(status_code=503, detail={"message": detail}) from exc
    except AgentUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": "ODS host agent is not reachable. Start the host agent, then try Apply changes again."},
        ) from exc
    except AgentProtocolError as exc:
        logger.exception("Settings apply failed")
        raise HTTPException(
            status_code=500,
            detail={"message": "Could not apply runtime changes via host agent."},
        ) from exc


# --- Service Health Polling ---

async def _get_services() -> list[ServiceStatus]:
    """Return cached service health, falling back to live check."""
    cached = get_cached_services()
    if cached is not None:
        return cached
    return await get_all_services()


async def _poll_service_health():
    """Background task: poll all service health on a timer.

    Results stored via set_services_cache(). API endpoints read
    cached results instead of running live checks. The poll can
    take as long as it needs — nobody waits for it.
    """
    await asyncio.sleep(2)  # let services start
    while True:
        try:
            statuses = await get_all_services()
            set_services_cache(statuses)
        except Exception:
            logger.exception("Service health poll failed")
        await asyncio.sleep(_SERVICE_POLL_INTERVAL)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("DASHBOARD_API_PORT", "3002")))
