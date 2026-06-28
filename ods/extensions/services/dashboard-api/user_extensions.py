"""Dynamic user extension manifest scanner with TTL cache."""

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_HEALTH_PATH_RE = re.compile(r"^/[A-Za-z0-9/_\-.]*$")
_HEALTH_PATH_REJECT = ("..", "@", "?", "#", "http://", "https://")
_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def scan_user_extension_services(
    user_ext_dir: Path,
) -> dict[str, dict[str, Any]]:
    """Scan user extensions directory for enabled services with health endpoints.

    Returns a dict keyed by service_id in the same format as ``SERVICES``
    from config.py, compatible with ``check_service_health()``.
    """
    services: dict[str, dict[str, Any]] = {}

    if not user_ext_dir.is_dir():
        return services

    for item in sorted(user_ext_dir.iterdir()):
        if item.is_symlink():
            continue

        if not item.is_dir():
            continue

        service_id = item.name

        if not _SERVICE_ID_RE.match(service_id):
            continue

        # Only enabled extensions (compose.yaml present, not .disabled)
        if not (item / "compose.yaml").exists():
            continue

        manifest_path = item / "manifest.yaml"
        if not manifest_path.exists():
            continue

        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError) as e:
            logger.debug("Skipping %s: bad manifest: %s", service_id, e)
            continue

        if not isinstance(manifest, dict):
            continue

        svc = manifest.get("service")
        if not isinstance(svc, dict):
            continue

        health = svc.get("health") or ""

        try:
            if health:
                # Validate health path (must be a string)
                if not isinstance(health, str):
                    raise TypeError(f"health must be a string, got {type(health).__name__}")
                if not _HEALTH_PATH_RE.match(health):
                    logger.warning("Rejected health path for %s: %r", service_id, health)
                    continue
                if any(bad in health for bad in _HEALTH_PATH_REJECT):
                    logger.warning("Rejected health path for %s: %r", service_id, health)
                    continue

            port = svc.get("port", 0)
            name = svc.get("name", service_id)

            # Host = service_id (Docker DNS). Never trust manifest host_env/default_host.
            services[service_id] = {
                "host": service_id,
                "port": int(port),
                "external_port": int(svc.get("external_port_default", port)),
                "health": health,
                "name": name,
                # Optional: extensions whose health endpoint lives on a
                # secondary port (e.g. milvus 9091) need an explicit
                # health_port; check_service_health() falls back to "port"
                # when absent.
                **({"health_port": int(svc["health_port"])} if "health_port" in svc else {}),
            }
        except (TypeError, ValueError) as exc:
            logger.warning("Skipping extension %s: invalid manifest value: %s", service_id, exc)
            continue

    return services


# --- TTL Cache ---

_cache: dict[str, Any] = {"result": {}, "timestamp": float("-inf")}
_cache_lock = threading.Lock()


def get_user_services_cached(
    user_ext_dir: Path, ttl: float = 30.0,
) -> dict[str, dict[str, Any]]:
    """Return cached result of ``scan_user_extension_services()``.

    Re-scans when *ttl* seconds have elapsed since the last scan.
    """
    with _cache_lock:
        now = time.monotonic()
        if now - _cache["timestamp"] < ttl:
            return _cache["result"].copy()

    result = scan_user_extension_services(user_ext_dir)
    with _cache_lock:
        _cache["result"] = result
        _cache["timestamp"] = time.monotonic()
    return result.copy()


def _reset_cache() -> None:
    """Clear the cached scan result. Used for test isolation."""
    with _cache_lock:
        _cache["result"] = {}
        _cache["timestamp"] = float("-inf")
