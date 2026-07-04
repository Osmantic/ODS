"""Node capabilities — one-call diagnostics snapshot (GPU, model, services, version)."""

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from config import INSTALL_DIR
from gpu import get_gpu_info
from helpers import get_all_services, get_loaded_model
from models import NodeCapabilities
from security import verify_api_key
import settings

router = APIRouter(tags=["node"])

# Service statuses that indicate the container is up (as opposed to down /
# not_deployed). Used to compute running_service_count.
_RUNNING_STATUSES = {"healthy", "unhealthy", "unknown"}


def _install_root() -> Path:
    """Resolve the ODS install root, preferring the in-container /ods mount.

    Mirrors main._resolve_install_root so the version read is consistent whether
    the API runs in the container (/ods) or on the host (INSTALL_DIR).
    """
    host_root = Path("/ods")
    if host_root.exists():
        return host_root
    return Path(INSTALL_DIR)


def _read_ods_version(fallback: str) -> str:
    """Read the installed ODS version from the install .env or .version file.

    Order: ODS_VERSION in .env, then .version (plain string or {"version": ...}).
    Falls back to *fallback* (the running app version) when neither is present.
    """
    root = _install_root()

    env_file = root / ".env"
    env_vars, _ = settings._read_env_map_from_path(env_file)
    if "ODS_VERSION" in env_vars and env_vars["ODS_VERSION"]:
        return env_vars["ODS_VERSION"]

    version_file = root / ".version"
    try:
        raw = version_file.read_text().strip()
        if raw:
            if raw.startswith("{"):
                data = json.loads(raw)
                if isinstance(data, dict) and data.get("version"):
                    return str(data["version"])
            else:
                return raw
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    return fallback


# ============================================================================
# Endpoints
# ============================================================================

@router.get(
    "/api/node/capabilities",
    response_model=NodeCapabilities,
    dependencies=[Depends(verify_api_key)],
)
async def node_capabilities(request: Request):
    """Aggregated node snapshot for diagnostics and support bundles.

    Combines GPU type/VRAM (see gpu.get_gpu_info), the currently loaded model,
    service health, and the installed ODS version in a single call. Read-only.
    running_service_count counts services whose container is up (healthy,
    unhealthy, or unknown), excluding down / not_deployed.
    """
    gpu_info, loaded_model, services = await asyncio.gather(
        asyncio.to_thread(get_gpu_info),
        get_loaded_model(),
        get_all_services(),
    )

    running = [s for s in services if s.status in _RUNNING_STATUSES]

    return NodeCapabilities(
        ods_version=_read_ods_version(request.app.version),
        gpu=gpu_info,
        loaded_model=loaded_model,
        services=services,
        service_count=len(services),
        running_service_count=len(running),
    )
