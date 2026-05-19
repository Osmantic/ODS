"""GPU router — per-GPU metrics, topology, and rolling history."""

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from security import verify_api_key

from gpu import (
    decode_gpu_assignment,
    get_gpu_info_amd_detailed,
    get_gpu_info_apple,
    get_gpu_info_nvidia_detailed,
    read_gpu_topology,
)
from models import GPUInfo, IndividualGPU, MultiGPUStatus
from models import AmdRuntimeStatus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["gpu"])

# Rolling history buffer — 60 samples max (5 min at 5 s intervals)
_GPU_HISTORY: deque = deque(maxlen=60)
_HISTORY_POLL_INTERVAL = 5.0

# Simple per-endpoint TTL caches
_detailed_cache: dict = {"expires": 0.0, "value": None}
_topology_cache: dict = {"expires": 0.0, "value": None}
_GPU_DETAILED_TTL = 3.0
_GPU_TOPOLOGY_TTL = 300.0


# ============================================================================
# Internal helpers
# ============================================================================

def _apple_info_to_individual(info: GPUInfo) -> IndividualGPU:
    """Wrap an Apple Silicon aggregate GPUInfo as a single IndividualGPU entry."""
    return IndividualGPU(
        index=0,
        uuid="apple-unified-0",  # 15 chars; GPUCard.jsx calls uuid.slice(-8)
        name=info.name,
        memory_used_mb=info.memory_used_mb,
        memory_total_mb=info.memory_total_mb,
        memory_percent=info.memory_percent,
        utilization_percent=info.utilization_percent,
        temperature_c=info.temperature_c,
        power_w=info.power_w,
        assigned_services=[],
    )


def _get_raw_gpus(gpu_backend: str) -> Optional[list[IndividualGPU]]:
    """Return per-GPU list from the appropriate backend, with fallback."""
    if gpu_backend == "apple":
        info = get_gpu_info_apple()
        if info is None:
            return None
        return [_apple_info_to_individual(info)]
    if gpu_backend == "amd":
        return get_gpu_info_amd_detailed()
    result = get_gpu_info_nvidia_detailed()
    if result:
        return result
    return get_gpu_info_amd_detailed()


def _build_aggregate(gpus: list[IndividualGPU], backend: str) -> GPUInfo:
    """Compute an aggregate GPUInfo from a list of IndividualGPU objects."""
    if len(gpus) == 1:
        g = gpus[0]
        return GPUInfo(
            name=g.name,
            memory_used_mb=g.memory_used_mb,
            memory_total_mb=g.memory_total_mb,
            memory_percent=g.memory_percent,
            utilization_percent=g.utilization_percent,
            temperature_c=g.temperature_c,
            power_w=g.power_w,
            gpu_backend=backend,
        )

    mem_used = sum(g.memory_used_mb for g in gpus)
    mem_total = sum(g.memory_total_mb for g in gpus)
    avg_util = round(sum(g.utilization_percent for g in gpus) / len(gpus))
    max_temp = max(g.temperature_c for g in gpus)
    pw_values = [g.power_w for g in gpus if g.power_w is not None]
    total_power: Optional[float] = round(sum(pw_values), 1) if pw_values else None

    names = [g.name for g in gpus]
    if len(set(names)) == 1:
        display_name = f"{names[0]} \u00d7 {len(gpus)}"
    else:
        display_name = " + ".join(names[:2])
        if len(names) > 2:
            display_name += f" + {len(names) - 2} more"

    return GPUInfo(
        name=display_name,
        memory_used_mb=mem_used,
        memory_total_mb=mem_total,
        memory_percent=round(mem_used / mem_total * 100, 1) if mem_total > 0 else 0.0,
        utilization_percent=avg_util,
        temperature_c=max_temp,
        power_w=total_power,
        gpu_backend=backend,
    )


def _clean_env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _join_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    suffix = path if path.startswith("/") else f"/{path}"
    return f"{base}{suffix}"


def _runtime_port() -> tuple[int, Optional[str]]:
    raw = _clean_env("AMD_INFERENCE_PORT")
    if not raw:
        return 8080, None
    try:
        port = int(raw)
    except ValueError:
        return 8080, "amd_port_invalid"
    if 1 <= port <= 65535:
        return port, None
    return 8080, "amd_port_invalid"


def _split_backend_list(raw: str) -> tuple[list[str], Optional[str]]:
    if not raw:
        return [], None

    backends: list[str] = []
    invalid: list[str] = []
    for item in raw.split(","):
        backend = item.strip().lower()
        if not backend:
            continue
        if backend in {"rocm", "vulkan"}:
            if backend not in backends:
                backends.append(backend)
        else:
            invalid.append(backend)
    if invalid:
        return backends, "amd_supported_backends_invalid"
    return backends, None


def _env_bool(name: str) -> bool:
    return _clean_env(name).lower() in {"1", "true", "yes", "on"}


def _runtime_base_url(runtime: str, location: str, port: int) -> str:
    if location == "host":
        return f"http://host.docker.internal:{port}"
    if location == "container":
        return f"http://llama-server:{port}"
    return (
        _clean_env("OLLAMA_URL")
        or _clean_env("LLM_URL")
        or _clean_env("LLM_API_URL")
        or "http://llama-server:8080"
    )


def _runtime_api_path(runtime: str) -> str:
    configured = _clean_env("LLM_API_BASE_PATH")
    if configured:
        return configured
    if runtime == "lemonade":
        return "/api/v1"
    return "/v1"


def _runtime_health_path(runtime: str, api_path: str) -> str:
    if runtime == "lemonade":
        return _join_url(api_path, "health")
    return "/health"


def _probe_amd_health(health_url: str) -> tuple[str, str, Optional[str]]:
    request = urllib.request.Request(health_url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            status = getattr(response, "status", response.getcode())
            body = response.read(4096).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return "unhealthy", "unknown", f"health_http_{exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.debug("AMD runtime health probe failed for %s: %s", health_url, exc)
        return "unreachable", "unknown", "health_unreachable"

    version = "unknown"
    try:
        payload = json.loads(body) if body else {}
        if isinstance(payload, dict) and payload.get("version"):
            version = str(payload["version"])
    except json.JSONDecodeError:
        pass

    if 200 <= int(status) < 300:
        return "reachable", version, None
    return "unhealthy", version, f"health_http_{status}"


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/api/gpu/detailed", response_model=MultiGPUStatus, dependencies=[Depends(verify_api_key)])
async def gpu_detailed():
    """Per-GPU metrics with service assignment info (cached 3 s)."""
    now = time.monotonic()
    if now < _detailed_cache["expires"] and _detailed_cache["value"] is not None:
        return _detailed_cache["value"]

    gpu_backend = os.environ.get("GPU_BACKEND", "").lower() or "nvidia"
    gpus = await asyncio.to_thread(_get_raw_gpus, gpu_backend)
    if not gpus:
        raise HTTPException(status_code=503, detail="No GPU data available")

    aggregate = _build_aggregate(gpus, gpu_backend)

    assignment_full = decode_gpu_assignment()
    assignment_data = assignment_full.get("gpu_assignment") if assignment_full else None

    result = MultiGPUStatus(
        gpu_count=len(gpus),
        backend=gpu_backend,
        gpus=gpus,
        topology=None,  # topology is served from its own endpoint
        assignment=assignment_data,
        split_mode=os.environ.get("LLAMA_ARG_SPLIT_MODE") or None,
        tensor_split=os.environ.get("LLAMA_ARG_TENSOR_SPLIT") or None,
        aggregate=aggregate,
    )
    _detailed_cache["expires"] = now + _GPU_DETAILED_TTL
    _detailed_cache["value"] = result
    return result


@router.get("/api/gpu/topology", dependencies=[Depends(verify_api_key)])
async def gpu_topology():
    """GPU topology from config/gpu-topology.json (written by installer / dream-cli). Cached 300 s."""
    now = time.monotonic()
    if now < _topology_cache["expires"] and _topology_cache["value"] is not None:
        return _topology_cache["value"]

    topo = await asyncio.to_thread(read_gpu_topology)
    if not topo:
        raise HTTPException(
            status_code=404,
            detail="GPU topology not available. Run 'dream gpu reassign' to generate it.",
        )

    _topology_cache["expires"] = now + _GPU_TOPOLOGY_TTL
    _topology_cache["value"] = topo
    return topo


@router.get(
    "/api/gpu/amd-runtime",
    response_model=AmdRuntimeStatus,
    response_model_exclude_none=True,
    dependencies=[Depends(verify_api_key)],
)
async def amd_runtime():
    """AMD runtime contract and health from explicit installer-provided env."""
    gpu_backend = _clean_env("GPU_BACKEND").lower() or "nvidia"
    if gpu_backend != "amd":
        return AmdRuntimeStatus(
            available=False,
            reason="not_amd",
            runtime="none",
            location="none",
            runtimeMode="none",
            managedByDreamServer=False,
            selectedBackend="none",
            supportedBackends=[],
            defaultBackend="none",
            capabilities=[],
            warnings=[],
        )

    warnings: list[str] = []
    runtime = _clean_env("AMD_INFERENCE_RUNTIME").lower()
    selected_backend = _clean_env("AMD_INFERENCE_BACKEND").lower()
    location = _clean_env("AMD_INFERENCE_LOCATION").lower()
    runtime_mode = _clean_env("AMD_INFERENCE_RUNTIME_MODE").lower()
    managed_raw = _clean_env("AMD_INFERENCE_MANAGED").lower()
    managed_by_dream_server = _env_bool("AMD_INFERENCE_MANAGED")
    supported_backends, supported_warning = _split_backend_list(
        _clean_env("AMD_INFERENCE_SUPPORTED_BACKENDS")
    )
    if supported_warning:
        warnings.append(supported_warning)

    if not runtime:
        legacy_backend = _clean_env("LLM_BACKEND").lower()
        if legacy_backend in {"lemonade", "llama-server"}:
            runtime = legacy_backend
            warnings.append("amd_runtime_env_missing")
    if not selected_backend:
        selected_backend = _clean_env("LEMONADE_LLAMACPP_BACKEND").lower() or "unknown"
        warnings.append("amd_backend_env_missing")
    if not location:
        location = "unknown"
        warnings.append("amd_location_env_missing")
    if not runtime_mode:
        runtime_mode = "unknown"
        warnings.append("amd_runtime_mode_env_missing")
    if not managed_raw:
        warnings.append("amd_managed_env_missing")
    if not supported_backends:
        warnings.append("amd_supported_backends_env_missing")
    elif selected_backend not in {"", "unknown", "none"} and selected_backend not in supported_backends:
        warnings.append("amd_selected_backend_not_supported")

    if runtime not in {"lemonade", "llama-server"}:
        return AmdRuntimeStatus(
            available=False,
            reason="runtime_not_configured",
            runtime=runtime or "none",
            location=location,
            runtimeMode=runtime_mode,
            managedByDreamServer=managed_by_dream_server,
            selectedBackend=selected_backend,
            supportedBackends=supported_backends,
            defaultBackend=selected_backend or "none",
            capabilities=supported_backends,
            warnings=warnings,
        )

    port, port_warning = _runtime_port()
    if port_warning:
        warnings.append(port_warning)

    api_path = _runtime_api_path(runtime)
    base_url = _runtime_base_url(runtime, location, port)
    api_base = _join_url(base_url, api_path)
    health_url = _join_url(base_url, _runtime_health_path(runtime, api_path))
    health, version, health_warning = await asyncio.to_thread(_probe_amd_health, health_url)
    if health_warning:
        warnings.append(health_warning)

    return AmdRuntimeStatus(
        available=True,
        reason=None,
        runtime=runtime,
        location=location,
        runtimeMode=runtime_mode,
        managedByDreamServer=managed_by_dream_server,
        selectedBackend=selected_backend,
        supportedBackends=supported_backends,
        defaultBackend=selected_backend or "none",
        apiBase=api_base,
        healthUrl=health_url,
        health=health,
        version=version,
        capabilities=supported_backends,
        warnings=warnings,
    )


@router.get("/api/gpu/history", dependencies=[Depends(verify_api_key)])
async def gpu_history():
    """Rolling 5-minute per-GPU metrics history sampled every 5 s."""
    if not _GPU_HISTORY:
        return {"timestamps": [], "gpus": {}}

    timestamps = [s["timestamp"] for s in _GPU_HISTORY]

    gpu_keys: set[str] = set()
    for sample in _GPU_HISTORY:
        gpu_keys.update(sample["gpus"].keys())

    gpus_data: dict[str, dict] = {}
    for gpu_key in sorted(gpu_keys):
        gpus_data[gpu_key] = {
            "utilization": [],
            "memory_percent": [],
            "temperature": [],
            "power_w": [],
        }
        for sample in _GPU_HISTORY:
            g = sample["gpus"].get(gpu_key, {})
            gpus_data[gpu_key]["utilization"].append(g.get("utilization", 0))
            gpus_data[gpu_key]["memory_percent"].append(g.get("memory_percent", 0))
            gpus_data[gpu_key]["temperature"].append(g.get("temperature", 0))
            gpus_data[gpu_key]["power_w"].append(g.get("power_w"))

    return {"timestamps": timestamps, "gpus": gpus_data}


# ============================================================================
# Background task
# ============================================================================

async def poll_gpu_history() -> None:
    """Background task: append a per-GPU sample to _GPU_HISTORY every 5 s."""
    while True:
        try:
            gpu_backend = os.environ.get("GPU_BACKEND", "").lower() or "nvidia"
            gpus = await asyncio.to_thread(_get_raw_gpus, gpu_backend)
            if gpus:
                sample = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "gpus": {
                        str(g.index): {
                            "utilization": g.utilization_percent,
                            "memory_percent": g.memory_percent,
                            "temperature": g.temperature_c,
                            "power_w": g.power_w,
                        }
                        for g in gpus
                    },
                }
                _GPU_HISTORY.append(sample)
        except Exception:  # Broad catch: background task must survive transient failures
            logger.exception("GPU history poll failed")
        await asyncio.sleep(_HISTORY_POLL_INTERVAL)
