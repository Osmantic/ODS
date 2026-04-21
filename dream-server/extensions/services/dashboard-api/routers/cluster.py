"""Cluster router — LAN cluster status and health monitoring."""

import asyncio
import json
import logging
import os
import socket
import time
from pathlib import Path

from fastapi import APIRouter, Depends

from config import INSTALL_DIR
from models import ClusterController, ClusterGPU, ClusterNode, ClusterStatus
from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cluster"])

CLUSTER_CONFIG = Path(INSTALL_DIR) / "config" / "cluster.json"
_HEALTH_POLL_INTERVAL = 10.0
_TCP_CHECK_TIMEOUT = 3.0

# Background health state: ip:port -> {online: bool, ping_ms: float|None}
_worker_health: dict[str, dict] = {}


def _load_cluster_config() -> dict:
    """Read cluster.json from disk. Returns empty state if missing."""
    if not CLUSTER_CONFIG.exists():
        return {"enabled": False, "controller": {}, "nodes": []}
    return json.loads(CLUSTER_CONFIG.read_text())


def _check_worker(host: str, port: int) -> tuple[bool, float | None]:
    """TCP connect check. Returns (reachable, ping_ms)."""
    start = time.monotonic()
    try:
        sock = socket.create_connection((host, port), timeout=_TCP_CHECK_TIMEOUT)
        sock.close()
        elapsed = (time.monotonic() - start) * 1000
        return True, round(elapsed, 1)
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False, None


@router.get("/api/cluster/status", response_model=ClusterStatus, dependencies=[Depends(verify_api_key)])
async def cluster_status():
    """Current cluster configuration and live health from background poller."""
    config = await asyncio.to_thread(_load_cluster_config)

    if not config.get("enabled"):
        return ClusterStatus(enabled=False)

    ctrl_raw = config.get("controller", {})
    controller = ClusterController(
        ip=ctrl_raw.get("ip", ""),
        gpu_backend=ctrl_raw.get("gpu_backend", ""),
        gpus=[ClusterGPU(**g) for g in ctrl_raw.get("gpus", [])],
    ) if ctrl_raw.get("ip") else None

    nodes = []
    for n in config.get("nodes", []):
        key = f"{n['ip']}:{n.get('rpc_port', 50052)}"
        health = _worker_health.get(key, {})
        nodes.append(ClusterNode(
            ip=n["ip"],
            rpc_port=n.get("rpc_port", 50052),
            gpu_backend=n.get("gpu_backend", ""),
            gpus=[ClusterGPU(**g) for g in n.get("gpus", [])],
            status="online" if health.get("online") else "offline",
            ping_ms=health.get("ping_ms"),
            added_at=n.get("added_at"),
        ))

    return ClusterStatus(
        enabled=True,
        controller=controller,
        nodes=nodes,
        tensor_split=os.environ.get("CLUSTER_TENSOR_SPLIT", ""),
        worker_list=os.environ.get("CLUSTER_WORKERS", ""),
    )


async def poll_cluster_health() -> None:
    """Background task: TCP-ping each worker every 10s, update _worker_health.

    Narrow catch: only swallow errors from reading/parsing the on-disk
    config. Worker TCP probes are already bounded inside `_check_worker`.
    Anything else (programming errors, CancelledError) propagates so the
    background task dies loudly rather than silently looping.
    """
    while True:
        try:
            config = await asyncio.to_thread(_load_cluster_config)
        except (OSError, json.JSONDecodeError):
            logger.exception("Cluster health poll: failed to read %s", CLUSTER_CONFIG)
            await asyncio.sleep(_HEALTH_POLL_INTERVAL)
            continue

        if config.get("enabled"):
            # Build the set of keys the current config actually references,
            # then prune any stale entries (workers that were removed from
            # cluster.json). Without this, _worker_health grows unbounded
            # across config rewrites.
            current_keys = {
                f"{n['ip']}:{n.get('rpc_port', 50052)}"
                for n in config.get("nodes", [])
            }
            for stale in set(_worker_health) - current_keys:
                _worker_health.pop(stale, None)

            for node in config.get("nodes", []):
                ip = node["ip"]
                port = node.get("rpc_port", 50052)
                online, ping_ms = await asyncio.to_thread(_check_worker, ip, port)
                _worker_health[f"{ip}:{port}"] = {"online": online, "ping_ms": ping_ms}
        else:
            # Cluster was disabled — clear anything we'd been tracking so that
            # re-enabling starts from a clean slate.
            _worker_health.clear()
        await asyncio.sleep(_HEALTH_POLL_INTERVAL)
