"""Per-service resource metrics."""

import asyncio
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from config import DATA_DIR, GPU_BACKEND, SERVICES
from helpers import dir_size_gb
from host_agent_client import (
    AgentClientError,
    AgentHTTPError,
    AgentProtocolError,
    AgentUnavailable,
    request_json as request_agent_json,
)
from security import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["resources"])
_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

_DATA_DIR_MAP = {
    "models": "llama-server",
    "qdrant": "qdrant",
    "open-webui": "open-webui",
    "langfuse": "langfuse",
    "n8n": "n8n",
    "comfyui": "comfyui",
    "tts": "tts",
    "whisper": "whisper",
}


def _service_restartability(config: dict) -> tuple[bool, str | None]:
    """Return whether a service can be restarted with docker restart."""
    service_type = config.get("type", "docker") or "docker"
    if service_type == "host-systemd":
        return False, "Host-level service; restart outside Docker"
    if service_type != "docker":
        return False, f"Service type {service_type} is not a Docker container"
    container_name = config.get("container_name")
    if not isinstance(container_name, str) or not container_name.strip():
        return False, "No Docker container is declared"
    return True, None


def _scan_service_disk() -> dict[str, dict]:
    """Scan /data/* directories and map to services."""
    data_path = Path(DATA_DIR)
    results = {}
    if not data_path.is_dir():
        return results
    for child in data_path.iterdir():
        if not child.is_dir():
            continue
        service_id = _DATA_DIR_MAP.get(child.name, child.name)
        size_gb = dir_size_gb(child)
        if size_gb > 0:
            results[service_id] = {"data_gb": size_gb, "path": f"data/{child.name}"}
    return results


def _fetch_container_stats() -> list[dict]:
    """Fetch container stats from host agent."""
    try:
        data = request_agent_json("GET", "/v1/service/stats", timeout=10)
        return data.get("containers", [])
    except (AgentClientError, OSError):
        logger.debug("Could not fetch container stats from host agent")
        return []


def _post_agent_json(path: str, body: dict, timeout: int = 65) -> dict:
    """POST JSON to the host agent and return its JSON response."""
    try:
        return request_agent_json("POST", path, payload=body, timeout=timeout)
    except AgentHTTPError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail or "Host agent request failed",
        ) from exc
    except AgentUnavailable as exc:
        logger.warning("Host agent unavailable at %s: %s", path, exc)
        raise HTTPException(status_code=503, detail=f"Host agent unavailable: {exc}") from exc
    except AgentProtocolError as exc:
        raise HTTPException(status_code=500, detail=f"Host agent call failed: {exc}") from exc


@router.get("/api/services/resources")
async def service_resources(api_key: str = Depends(verify_api_key)):
    """Get per-service resource metrics (CPU, RAM, disk)."""
    from main import _cache  # noqa: PLC0415 — deferred import to avoid circular dependency

    container_stats = _cache.get("service_resources_containers")
    disk_usage = _cache.get("service_resources_disk")

    need_containers = container_stats is None
    need_disk = disk_usage is None

    if need_containers or need_disk:
        tasks = []
        if need_containers:
            tasks.append(asyncio.to_thread(_fetch_container_stats))
        if need_disk:
            tasks.append(asyncio.to_thread(_scan_service_disk))

        results = await asyncio.gather(*tasks)
        idx = 0
        if need_containers:
            container_stats = results[idx]
            idx += 1
            _cache.set("service_resources_containers", container_stats, 20)
        if need_disk:
            disk_usage = results[idx]
            _cache.set("service_resources_disk", disk_usage, 60)

    container_stats = container_stats or []
    disk_usage = disk_usage or {}

    # Build reverse map: container_name -> service_id from SERVICES dict.
    # This correctly handles non-standard names (ods-webui -> open-webui)
    # when container_name is populated in SERVICES (by PR E's config.py change).
    # Falls back to ods-{sid} convention when container_name is missing.
    container_to_service = {}
    for sid, svc in SERVICES.items():
        cname = svc.get("container_name", f"ods-{sid}")
        if isinstance(cname, str) and cname.strip():
            container_to_service[cname] = sid

    stats_by_id = {}
    for stat in container_stats:
        cname = stat.get("container_name", "")
        mapped_id = container_to_service.get(cname, stat.get("service_id", cname))
        stats_by_id[mapped_id] = stat

    services = []
    for service_id, config in SERVICES.items():
        restartable, restart_unavailable_reason = _service_restartability(config)
        entry = {
            "id": service_id,
            "name": config["name"],
            "type": config.get("type", "docker") or "docker",
            "restartable": restartable,
            "restart_unavailable_reason": restart_unavailable_reason,
            "container": stats_by_id.get(service_id),
            "disk": disk_usage.get(service_id),
        }
        services.append(entry)

    # Add services with disk data but not in SERVICES dict (orphaned data)
    known_ids = set(SERVICES.keys())
    for sid, disk in disk_usage.items():
        if sid not in known_ids:
            services.append({
                "id": sid,
                "name": sid,
                "type": "unknown",
                "restartable": False,
                "restart_unavailable_reason": "Service is not declared in the active manifest set",
                "container": None,
                "disk": disk,
            })

    total_cpu = sum(s.get("cpu_percent", 0) for s in container_stats)
    total_mem = sum(s.get("memory_used_mb", 0) for s in container_stats)
    total_disk = sum(d.get("data_gb", 0) for d in disk_usage.values())

    return {
        "services": services,
        "totals": {
            "cpu_percent": round(total_cpu, 1),
            "memory_used_mb": round(total_mem),
            "disk_data_gb": round(total_disk, 2),
        },
        "caveats": {
            "docker_desktop_memory": GPU_BACKEND == "apple",
        },
    }


@router.post("/api/services/{service_id}/restart")
async def restart_service(service_id: str, api_key: str = Depends(verify_api_key)):
    """Restart a single known ODS service via the host agent."""
    if not _SERVICE_ID_RE.match(service_id):
        raise HTTPException(status_code=400, detail="Invalid service_id")
    if service_id not in SERVICES:
        raise HTTPException(status_code=404, detail=f"Service not found: {service_id}")
    restartable, restart_unavailable_reason = _service_restartability(SERVICES[service_id])
    if not restartable:
        raise HTTPException(
            status_code=400,
            detail=restart_unavailable_reason or f"Service cannot be restarted: {service_id}",
        )

    body = {"service_id": service_id}
    if service_id in {"dashboard", "dashboard-api"}:
        # Restarting the UI proxy or the API synchronously can kill the very
        # request that initiated it. Let the host agent acknowledge first.
        body["delay_seconds"] = 1

    result = await asyncio.to_thread(
        _post_agent_json,
        "/v1/service/restart",
        body,
    )

    from main import _cache  # noqa: PLC0415 — deferred import to avoid circular dependency
    _cache.invalidate("service_resources_containers")
    return result
