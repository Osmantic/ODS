"""Dynamic user extension manifest scanner with TTL cache."""

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from config import _read_env_value

logger = logging.getLogger(__name__)

_HEALTH_PATH_RE = re.compile(r"^/[A-Za-z0-9/_\-.]*$")
_HEALTH_PATH_REJECT = ("..", "@", "?", "#", "http://", "https://")
_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _public_url(service_id: str, svc: dict) -> str:
    """Project only an explicitly declared, extension-owned nonsecret URL."""
    key = svc.get("public_url_env")
    if key is None:
        return ""
    prefix = service_id.upper().replace("-", "_") + "_"
    declarations = svc.get("env_vars", [])
    if (not isinstance(key, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", key)
            or not key.startswith(prefix) or not isinstance(declarations, list)):
        raise ValueError("public URL must reference an extension-owned declared nonsecret setting")
    matches = [entry for entry in declarations if isinstance(entry, dict) and entry.get("key") == key]
    if len(matches) != 1 or matches[0].get("secret", False) is not False:
        raise ValueError("public URL must reference an extension-owned declared nonsecret setting")
    value = _read_env_value(key) or matches[0].get("default", "")
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("invalid public URL")
    value = value.strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if (any(ord(char) < 32 or ord(char) == 127 for char in value)
            or "\\" in value or parsed.scheme not in {"http", "https"}
            or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise ValueError("invalid public URL")
    # Accessing port also rejects malformed/out-of-range authorities.
    if parsed.port == 0:
        raise ValueError("invalid public URL")
    return value.rstrip("/")


def _manifest_port(value: Any, *, allow_zero: bool = False) -> int:
    if type(value) is not int and not (
        isinstance(value, str) and re.fullmatch(r"[0-9]{1,5}", value.strip())
    ):
        raise ValueError("port must be an integer")
    port = int(value)
    if not (0 if allow_zero else 1) <= port <= 65535:
        raise ValueError("port outside valid range")
    return port


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
            port_int = _manifest_port(port)
            # Zero is the manifest's valid sentinel for no published host port.
            ext_port = _manifest_port(svc.get("external_port_default", port_int), allow_zero=True)
            name = str(svc.get("name") or service_id)
            public_url = _public_url(service_id, svc)

            health_auth_env = svc.get("health_auth_env")
            if health_auth_env is not None:
                prefix = service_id.upper().replace("-", "_") + "_"
                declared = svc.get("env_vars", [])
                if (not isinstance(health_auth_env, str)
                        or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", health_auth_env)
                        or not health_auth_env.startswith(prefix)
                        or not isinstance(declared, list)
                        or not any(isinstance(item, dict) and item.get("key") == health_auth_env
                                   and item.get("secret") is True for item in declared)):
                    raise ValueError("health authentication must reference an extension-owned declared secret")

            # Host = service_id (Docker DNS). Never trust manifest host_env/default_host.
            services[service_id] = {
                "host": service_id,
                "port": port_int,
                "external_port": ext_port,
                "health": health,
                "name": name,
                **({"public_url": public_url} if public_url else {}),
                **({"health_auth_env": health_auth_env} if health_auth_env is not None else {}),
                # Optional: extensions whose health endpoint lives on a
                # secondary port (e.g. milvus 9091) need an explicit
                # health_port; check_service_health() falls back to "port"
                # when absent.
                **({"health_port": _manifest_port(svc["health_port"])} if "health_port" in svc else {}),
            }
        except (TypeError, ValueError) as exc:
            logger.warning("Skipping extension %s: invalid manifest value: %s", service_id, exc)
            continue

    return services


# --- TTL Cache ---

_cache: dict[str, Any] = {}
_cache_lock = threading.Lock()


def get_user_services_cached(
    user_ext_dir: Path, ttl: float = 30.0,
) -> dict[str, dict[str, Any]]:
    """Return cached result of ``scan_user_extension_services()``.

    Re-scans when *ttl* seconds have elapsed since the last scan for the given directory.
    """
    cache_key = str(user_ext_dir.resolve()) if hasattr(user_ext_dir, "resolve") else str(user_ext_dir)
    with _cache_lock:
        now = time.monotonic()
        entry = _cache.get(cache_key)
        if entry and (now - entry["timestamp"] < ttl):
            return entry["result"].copy()

    result = scan_user_extension_services(user_ext_dir)
    with _cache_lock:
        _cache[cache_key] = {
            "result": result,
            "timestamp": time.monotonic(),
        }
    return result.copy()


def _reset_cache() -> None:
    """Clear the cached scan result. Used for test isolation."""
    with _cache_lock:
        _cache.clear()
