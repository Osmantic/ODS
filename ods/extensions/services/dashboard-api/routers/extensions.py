"""Extensions portal endpoints."""

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from config import (
    AGENT_URL, ALWAYS_ON_SERVICES, CORE_SERVICE_IDS, DATA_DIR,
    ODS_AGENT_KEY, EXTENSION_CATALOG, EXTENSIONS_DIR,
    EXTENSIONS_LIBRARY_DIR, GPU_BACKEND, SERVICES,
    USER_EXTENSIONS_DIR,
)
from security import verify_api_key

try:
    import fcntl
except ImportError:  # pragma: no cover - only hit on Windows hosts
    fcntl = None
    import msvcrt
else:  # pragma: no cover - platform branch
    msvcrt = None

logger = logging.getLogger(__name__)

router = APIRouter(tags=["extensions"])

_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_MAX_EXTENSION_BYTES = 50 * 1024 * 1024  # 50 MB


def _is_stale(iso_timestamp: str, max_age_seconds: int) -> bool:
    """Check if an ISO timestamp is older than max_age_seconds."""
    try:
        ts = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return age > max_age_seconds
    except (ValueError, TypeError, AttributeError):
        return True


def _read_progress(service_id: str) -> dict | None:
    """Read progress file for a service. Returns None if no active progress."""
    progress_file = Path(DATA_DIR) / "extension-progress" / f"{service_id}.json"
    if not progress_file.exists():
        return None
    try:
        data = json.loads(progress_file.read_text(encoding="utf-8"))
        updated = data.get("updated_at", "")
        if updated and _is_stale(updated, max_age_seconds=3600):
            if data.get("status") not in ("error",):
                return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _cleanup_stale_progress() -> None:
    """Remove progress files in terminal state past their TTL."""
    progress_dir = Path(DATA_DIR) / "extension-progress"
    if not progress_dir.is_dir():
        return
    for f in progress_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("status") == "started" and _is_stale(data.get("updated_at", ""), 900):
                f.unlink(missing_ok=True)
            elif _is_stale(data.get("updated_at", ""), 3600):
                f.unlink(missing_ok=True)
        except (json.JSONDecodeError, OSError):
            pass


def _write_initial_progress(service_id: str) -> None:
    """Write an initial progress file so the UI sees 'installing' immediately."""
    progress_dir = Path(DATA_DIR) / "extension-progress"
    progress_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    progress = {
        "service_id": service_id,
        "status": "pulling",
        "phase_label": "Starting installation...",
        "error": None,
        "started_at": now,
        "updated_at": now,
    }
    progress_file = progress_dir / f"{service_id}.json"
    progress_file.write_text(json.dumps(progress), encoding="utf-8")


def _write_error_progress(service_id: str, error_msg: str) -> None:
    """Update progress file to error state so the UI stops spinning."""
    progress_file = Path(DATA_DIR) / "extension-progress" / f"{service_id}.json"
    now = datetime.now(timezone.utc).isoformat()
    data = {"service_id": service_id, "status": "pulling", "error": None,
            "phase_label": "", "started_at": now, "updated_at": now}
    try:
        if progress_file.exists():
            data = json.loads(progress_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read progress file for %s: %s", service_id, exc)
    data["status"] = "error"
    data["error"] = error_msg
    data["updated_at"] = now
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    progress_file.write_text(json.dumps(data), encoding="utf-8")


def _has_error_progress(service_id: str) -> bool:
    progress = _read_progress(service_id)
    return bool(progress and progress.get("status") == "error")


def _clear_progress(service_id: str) -> None:
    progress_file = Path(DATA_DIR) / "extension-progress" / f"{service_id}.json"
    try:
        progress_file.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Failed to clear progress file for %s: %s", service_id, exc)


def _sync_extension_config(service_id: str) -> bool:
    """Ask host agent to copy config/<id>/ from an installed extension
    into INSTALL_DIR/config/.

    Some extensions ship a config/ subdirectory whose files are
    bind-mounted by compose.yaml relative to the compose project root
    (INSTALL_DIR), not relative to the extension directory.  Without
    this sync, Docker auto-creates the mount source as an empty
    directory, and the container fails at startup.

    The dashboard-api container has /ods/config bind-mounted
    read-only, so the actual copy is delegated to the host agent.
    Returns True on success (including the no-op case where the
    extension has no config/ subdir), False if the agent rejected the
    request or was unreachable.
    """
    return _call_agent_sync_config(service_id)


def _is_one_shot_extension(ext: dict) -> bool:
    """Return whether the catalog entry represents a one-shot CLI/setup tool.

    Prefer the explicit catalog copy of ``service.startup_check: false``. Fall
    back to ``port: 0`` for catalogs generated before that field was exposed.
    """
    if "startup_check" in ext:
        return ext.get("startup_check") is False
    return ext.get("port") == 0


def _compute_extension_status(ext: dict, services_by_id: dict) -> str:
    """Compute the runtime status of an extension."""
    ext_id = ext["id"]
    one_shot = _is_one_shot_extension(ext)

    # Check for in-flight install operations (progress files take priority)
    progress = _read_progress(ext_id)
    if progress:
        ps = progress.get("status", "")
        if ps in ("pulling", "starting"):
            # If the progress was never updated by the host agent (started_at == updated_at)
            # and is older than 2 min, the agent likely never picked it up — ignore.
            started = progress.get("started_at", "")
            updated = progress.get("updated_at", "")
            if started == updated and _is_stale(updated, max_age_seconds=120):
                pass  # fall through to normal status logic
            else:
                return "installing"
        if ps == "setup_hook":
            return "setting_up"
        if ps == "error":
            return "error"
        if ps == "started":
            # Container was started by the installer. If the progress is
            # recent (<5 min), the healthcheck may still be running —
            # show "installing". If older, the user likely stopped the
            # container afterwards — fall through to normal status logic.
            if not _is_stale(progress.get("updated_at", ""), max_age_seconds=300):
                # One-shot CLI tools (port=0, no healthcheck) reach a terminal
                # success state the moment compose returns 0 — surface that
                # explicitly so the dashboard stops polling and shows the
                # CLI-tool guidance instead of looping on a non-existent
                # health endpoint.
                if one_shot:
                    return "cli_installed"
                svc = services_by_id.get(ext_id)
                if not (svc and svc.status == "healthy"):
                    return "installing"

    # Core service loaded from manifests
    if ext_id in SERVICES:
        svc = services_by_id.get(ext_id)
        if svc and svc.status == "healthy":
            return "enabled"
        return "disabled"

    # User-installed extension — health-based when compose.yaml exists
    user_dir = USER_EXTENSIONS_DIR / ext_id
    if user_dir.is_dir():
        if (user_dir / "compose.yaml").exists():
            # One-shot CLI extensions don't expose a healthcheck — once
            # installed they're permanently in the "ready to invoke"
            # cli_installed state until uninstalled.
            if one_shot:
                return "cli_installed"
            svc = services_by_id.get(ext_id)
            if svc and svc.status == "healthy":
                return "enabled"
            # HTTP 4xx/5xx from the health endpoint is the clearest "container
            # is up but broken" signal — surface it as "unhealthy" so the UI
            # can prompt a log check. Timeouts / connection refused / DNS
            # failures stay "stopped" because they don't distinguish a crashed
            # container from an intentionally-stopped one.
            if svc and svc.status == "unhealthy":
                return "unhealthy"
            return "stopped"
        if (user_dir / "compose.yaml.disabled").exists():
            return "disabled"

    # GPU incompatibility
    gpu_backends = ext.get("gpu_backends", [])
    if gpu_backends and "all" not in gpu_backends and GPU_BACKEND not in gpu_backends:
        return "incompatible"

    return "not_installed"


def _is_installable(ext_id: str) -> bool:
    """Check if an extension is available in the extensions library."""
    # Require a deployable compose.yaml — a directory with only compose.yaml.disabled
    # or compose.yaml.reference cannot actually deploy and must not be advertised.
    ext_dir = EXTENSIONS_LIBRARY_DIR / ext_id
    return ext_dir.is_dir() and (ext_dir / "compose.yaml").exists()


def _validate_service_id(service_id: str) -> None:
    """Validate service_id format, raising 404 if invalid."""
    if not _SERVICE_ID_RE.match(service_id):
        raise HTTPException(status_code=404, detail=f"Invalid service_id: {service_id}")


def _assert_not_core(service_id: str) -> None:
    """Raise 403 if the service_id is an always-on base-compose service.

    Only blocks the 4 services from docker-compose.base.yml (llama-server,
    open-webui, dashboard, dashboard-api). Built-in extensions (n8n, tts, etc.)
    are allowed through because they're managed via compose.yaml toggle.
    """
    if service_id in ALWAYS_ON_SERVICES:
        raise HTTPException(
            status_code=403, detail=f"Cannot modify always-on service: {service_id}",
        )

def _resolve_extension_dir(service_id: str) -> Path:
    """Resolve an extension's directory, checking user-extensions first, then built-in.

    Raises HTTPException(404) if not found or path traversal is detected.
    """
    user_dir = (USER_EXTENSIONS_DIR / service_id).resolve()
    if user_dir.is_relative_to(USER_EXTENSIONS_DIR.resolve()) and user_dir.is_dir():
        return user_dir

    builtin_dir = (EXTENSIONS_DIR / service_id).resolve()
    if builtin_dir.is_relative_to(EXTENSIONS_DIR.resolve()) and builtin_dir.is_dir():
        return builtin_dir

    raise HTTPException(
        status_code=404, detail=f"Extension not found: {service_id}",
    )


# Compose port-binding host part may be a literal IP or a Compose variable
# expansion. To stay default-secure while supporting the LAN toggle (PR #964),
# we accept exactly two forms:
#   1. literal "127.0.0.1"
#   2. "${VAR:-127.0.0.1}" — variable with a literal-127.0.0.1 default
# Everything else (bare "${VAR}" with no default, "${VAR:-0.0.0.0}", literal
# "0.0.0.0", hostnames, etc.) is rejected.
_LOOPBACK_VAR_DEFAULT_RE = re.compile(
    r"^\$\{[A-Za-z_][A-Za-z0-9_]*:-127\.0\.0\.1\}$",
)


def _host_part_is_loopback(host: str) -> bool:
    if host == "127.0.0.1":
        return True
    # fullmatch (not match) so trailing characters never sneak past the
    # `$`-anchor — Python's `$` matches before a single trailing newline by
    # default, which YAML won't normally produce but is worth defending.
    return bool(_LOOPBACK_VAR_DEFAULT_RE.fullmatch(host))


def _split_port_host(port_str: str) -> tuple[Optional[str], str]:
    """Split a list-form port string into (host_part, rest).

    Naive ``str.split(":")`` is wrong for the sanctioned ``${VAR:-127.0.0.1}``
    pattern because the ``:-`` default operator contains a colon. We detect
    the variable-expansion prefix and consume up to its closing brace before
    splitting the remainder on the next colon.

    Returns ``(None, port_str)`` when there is no explicit host part
    (e.g. ``"8080:80"`` or bare ``"8080"`` — both bind 0.0.0.0 and are
    rejected by the caller).
    """
    if port_str.startswith("${"):
        end = port_str.find("}")
        if end == -1 or end + 1 >= len(port_str) or port_str[end + 1] != ":":
            # Malformed expansion or no host:port separator after it.
            return port_str, ""
        return port_str[: end + 1], port_str[end + 2:]
    if ":" not in port_str:
        return None, port_str
    host, _, rest = port_str.partition(":")
    if host.isdigit():
        # 2-part "host_port:container_port" — implicit 0.0.0.0, no host_ip.
        return None, port_str
    return host, rest


def _scan_compose_content(
    compose_path: Path,
    *,
    trusted: bool = False,
    skip_name_collision: bool = False,
    skip_gpu_passthrough_check: bool = False,
    skip_root_user_check: bool = False,
) -> None:
    """Reject compose files containing dangerous directives."""
    allowed_trusted_extra_hosts = {"host.docker.internal:host-gateway"}

    try:
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid compose file: {e}")

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=400, detail="Compose file must be a YAML mapping",
        )

    services = data.get("services", {})
    if not isinstance(services, dict):
        return

    for svc_name in services:
        if not skip_name_collision and svc_name in CORE_SERVICE_IDS:
            raise HTTPException(
                status_code=400,
                detail=f"Extension rejected: service name '{svc_name}' conflicts with core service",
            )

    for svc_name, svc_def in services.items():
        if not isinstance(svc_def, dict):
            continue
        if svc_def.get("privileged") is True:
            raise HTTPException(
                status_code=400,
                detail=f"Service '{svc_name}' uses privileged mode",
            )
        # Block Docker Compose internal label spoofing
        labels = svc_def.get("labels", [])
        if isinstance(labels, dict):
            label_keys = labels.keys()
        elif isinstance(labels, list):
            label_keys = [lbl.split("=", 1)[0] for lbl in labels if isinstance(lbl, str)]
        else:
            label_keys = []
        for lk in label_keys:
            if lk.startswith("com.docker.compose."):
                raise HTTPException(
                    status_code=400,
                    detail=f"Extension rejected: reserved Docker Compose label '{lk}' in service '{svc_name}'",
                )
        volumes = svc_def.get("volumes", [])
        if isinstance(volumes, list):
            for vol in volumes:
                vol_str = str(vol)
                if "docker.sock" in vol_str:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Extension rejected: Docker socket mount in {svc_name}",
                    )
                # Resolve the host-side source for both short-form
                # ("source:target[:opts]") and long-form ({type: bind,
                # source: ...}) entries, so a long-form bind can't slip an
                # absolute/relative host path past the string-only check.
                if isinstance(vol, dict):
                    vol_source = str(vol.get("source", ""))
                else:
                    vol_source = vol_str.split(":", 1)[0]
                if vol_source.startswith("/"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Extension rejected: absolute host path mount '{vol_source}' in {svc_name}",
                    )
                if ".." in vol_source.split("/"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Extension rejected: relative host path mount '{vol_source}' escaping the project directory in {svc_name}",
                    )
        cap_add = svc_def.get("cap_add", [])
        if isinstance(cap_add, list):
            for cap in cap_add:
                if str(cap).upper() in {
                    "SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE", "NET_RAW",
                    "DAC_OVERRIDE", "SETUID", "SETGID", "SYS_MODULE",
                    "SYS_RAWIO", "ALL",
                }:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Service '{svc_name}' adds dangerous capability: {cap}",
                    )
        if svc_def.get("pid") == "host":
            raise HTTPException(
                status_code=400,
                detail=f"Service '{svc_name}' uses host PID namespace",
            )
        if svc_def.get("network_mode") == "host":
            raise HTTPException(
                status_code=400,
                detail=f"Service '{svc_name}' uses host network mode",
            )
        if svc_def.get("ipc") == "host":
            raise HTTPException(
                status_code=400,
                detail=f"Service '{svc_name}' uses host IPC namespace",
            )
        if svc_def.get("userns_mode") == "host":
            raise HTTPException(
                status_code=400,
                detail=f"Service '{svc_name}' uses host user namespace",
            )
        if not skip_root_user_check:
            user = svc_def.get("user")
            if user is not None and str(user).split(":")[0] in ("root", "0"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Service '{svc_name}' runs as root",
                )
        if not trusted and "build" in svc_def:
            raise HTTPException(
                status_code=400,
                detail=f"Service '{svc_name}' uses a local build — only pre-built images are allowed for user extensions",
            )
        extra_hosts = svc_def.get("extra_hosts")
        if extra_hosts and not trusted:
            raise HTTPException(
                status_code=400,
                detail=f"Extension rejected: extra_hosts in {svc_name}",
            )
        if extra_hosts and trusted:
            if not isinstance(extra_hosts, list):
                raise HTTPException(
                    status_code=400,
                    detail=f"Extension rejected: unsupported extra_hosts in {svc_name}",
                )
            for entry in extra_hosts:
                if not isinstance(entry, str) or entry.strip() not in allowed_trusted_extra_hosts:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Extension rejected: unsupported extra_hosts in {svc_name}",
                    )
        if svc_def.get("sysctls"):
            raise HTTPException(
                status_code=400,
                detail=f"Extension rejected: sysctls in {svc_name}",
            )
        security_opt = svc_def.get("security_opt", [])
        if isinstance(security_opt, list):
            for opt in security_opt:
                opt_str = str(opt).lower().replace("=", ":")
                if opt_str in ("seccomp:unconfined", "apparmor:unconfined", "label:disable"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Extension rejected: dangerous security_opt '{opt}' in {svc_name}",
                    )
        if svc_def.get("devices"):
            raise HTTPException(
                status_code=400,
                detail=f"Extension rejected: devices in {svc_name}",
            )
        # Block Docker Compose v2 GPU passthrough for user extensions.
        # Built-ins (e.g. docker-compose.nvidia.yml) legitimately request
        # NVIDIA devices via deploy.resources.reservations.devices, so the
        # caller passes skip_gpu_passthrough_check=True for those.
        #
        # Each level checked with isinstance: a malformed compose like
        # `deploy: { resources: null }` or `resources: { reservations: null }`
        # would otherwise AttributeError on .get() and surface as a 500
        # instead of a clean scanner pass-through (no GPU request → no block).
        if not skip_gpu_passthrough_check:
            deploy = svc_def.get("deploy")
            if isinstance(deploy, dict):
                resources = deploy.get("resources")
                if isinstance(resources, dict):
                    reservations = resources.get("reservations")
                    if isinstance(reservations, dict) and reservations.get("devices"):
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"Extension rejected: GPU passthrough via "
                                f"deploy.resources.reservations.devices is not "
                                f"permitted in user extensions ({svc_name})"
                            ),
                        )
        ports = svc_def.get("ports", [])
        for port in ports:
            if isinstance(port, dict):
                # Dict-form: {target: 80, published: 8080, host_ip: ...}
                host_ip = port.get("host_ip", "")
                if port.get("published") and not _host_part_is_loopback(host_ip):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Extension rejected: dict port binding in {svc_name} "
                            f"must use host_ip: 127.0.0.1 (or '${{VAR:-127.0.0.1}}')"
                        ),
                    )
            else:
                port_str = str(port)
                host_part, rest = _split_port_host(port_str)
                if host_part is None:
                    # No host_ip — Docker binds 0.0.0.0.
                    label = "bare port" if ":" not in port_str else "port binding"
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Extension rejected: {label} '{port_str}' in {svc_name} "
                            f"must use 127.0.0.1:host:container format"
                        ),
                    )
                if not _host_part_is_loopback(host_part):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Extension rejected: port binding '{port_str}' "
                            f"in {svc_name} must bind 127.0.0.1 "
                            f"(literal or '${{VAR:-127.0.0.1}}')"
                        ),
                    )
                # Strip optional "/proto" suffix before checking host_port:container_port.
                core = rest.split("/", 1)[0]
                if ":" not in core:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Extension rejected: port binding '{port_str}' "
                            f"in {svc_name} must specify host:host_port:container_port"
                        ),
                    )

    # Scan top-level named volumes for bind-mount backdoors via driver_opts
    top_volumes = data.get("volumes", {})
    if isinstance(top_volumes, dict):
        for vol_name, vol_def in top_volumes.items():
            if not isinstance(vol_def, dict):
                continue
            driver_opts = vol_def.get("driver_opts", {})
            if not isinstance(driver_opts, dict):
                continue
            vol_type = str(driver_opts.get("type", "")).lower()
            device = str(driver_opts.get("device", ""))
            if vol_type in ("none", "bind") and device.startswith("/"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Extension rejected: named volume '{vol_name}' uses driver_opts to bind-mount host path '{device}'",
                )


def _ignore_special(directory: str, files: list[str]) -> list[str]:
    """Return files that should be skipped during copytree (symlinks, special)."""
    ignored = []
    for f in files:
        full = os.path.join(directory, f)
        try:
            st = os.lstat(full)
            if (stat.S_ISLNK(st.st_mode) or stat.S_ISFIFO(st.st_mode)
                    or stat.S_ISBLK(st.st_mode) or stat.S_ISCHR(st.st_mode)
                    or stat.S_ISSOCK(st.st_mode)):
                ignored.append(f)
        except OSError:
            ignored.append(f)
    return ignored


def _copytree_safe(src: Path, dst: Path) -> None:
    """Copy directory tree, skipping symlinks and special files."""
    shutil.copytree(src, dst, ignore=_ignore_special)


def _get_service_data_info(service_id: str) -> dict | None:
    """Return data directory info for a service, or None if no data dir exists."""
    from helpers import dir_size_gb  # noqa: PLC0415 — deferred to avoid circular import at module level
    data_path = (Path(DATA_DIR) / service_id).resolve()
    if not data_path.is_relative_to(Path(DATA_DIR).resolve()):
        return None
    if not data_path.is_dir():
        return None
    size_gb = dir_size_gb(data_path)
    return {
        "path": f"data/{service_id}",
        "size_gb": size_gb,
        "preserved": True,
        "purge_command": f"ods purge {service_id}",
    }


# --- Host Agent Helpers ---

_AGENT_TIMEOUT = 300  # seconds — image pulls can take several minutes on first install
_AGENT_LOG_TIMEOUT = 30  # seconds — log fetches should be fast


def _fetch_agent_logs(url: str, headers: dict, data: bytes, timeout: int) -> str:
    """Blocking POST to host agent that returns the response body as text.

    Extracted so async handlers can offload the urllib call via
    ``asyncio.to_thread``. urllib.error.HTTPError / URLError raised inside
    propagate back to the caller and are handled there.
    """
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode()


def _call_agent(action: str, service_id: str) -> bool:
    """Call host agent to start/stop a service. Returns True on success.

    Accepts both 200 (synchronous completion) and 202 (host agent kicked off a
    background retry — caller should let the dashboard's progress poll surface
    the eventual outcome). Mirrors _call_agent_install's contract.
    """
    url = f"{AGENT_URL}/v1/extension/{action}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ODS_AGENT_KEY}",
    }
    data = json.dumps({"service_id": service_id}).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_AGENT_TIMEOUT) as resp:
            return resp.status in (200, 202)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc:
        logger.warning(
            "Host agent unreachable at %s — fallback to restart_required: %s",
            AGENT_URL, exc,
        )
        return False


def _call_agent_invalidate_compose_cache() -> None:
    """Ask host agent to drop the .compose-flags cache after a compose mutation."""
    url = f"{AGENT_URL}/v1/compose/invalidate-cache"
    headers = {"Authorization": f"Bearer {ODS_AGENT_KEY}"}
    req = urllib.request.Request(url, data=b"", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_AGENT_LOG_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning(
                    "compose-flags cache invalidation returned HTTP %d", resp.status,
                )
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc:
        logger.warning(
            "Host agent unreachable for compose-flags invalidation at %s: %s",
            AGENT_URL, exc,
        )


def _call_agent_setup_hook(service_id: str) -> bool:
    """Call host agent to run setup_hook for an extension. Returns True on success.

    Backwards-compatible wrapper — delegates to the generic hook endpoint
    with hook_name="post_install".
    """
    return _call_agent_hook(service_id, "post_install")


def _call_agent_hook(service_id: str, hook_name: str) -> bool:
    """Call host agent to run a lifecycle hook. Returns True on success."""
    url = f"{AGENT_URL}/v1/extension/hooks"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ODS_AGENT_KEY}",
    }
    data = json.dumps({"service_id": service_id, "hook": hook_name}).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_AGENT_TIMEOUT) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # No hook defined — not an error
            return True
        logger.warning("%s hook failed for %s (HTTP %d)", hook_name, service_id, exc.code)
        return False
    except (urllib.error.URLError, OSError, TimeoutError):
        logger.warning("Host agent unreachable for %s hook at %s", hook_name, AGENT_URL)
        return False


def _call_agent_install(service_id: str) -> bool:
    """Call host agent combined install endpoint."""
    url = f"{AGENT_URL}/v1/extension/install"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ODS_AGENT_KEY}",
    }
    data = json.dumps({
        "service_id": service_id,
        "run_setup_hook": True,
    }).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_AGENT_TIMEOUT) as resp:
            return resp.status in (200, 202)
    except urllib.error.HTTPError as exc:
        logger.warning("Host agent install failed for %s (HTTP %d)", service_id, exc.code)
        return False
    except urllib.error.URLError as exc:
        logger.warning("Host agent unreachable for install at %s: %s", AGENT_URL, exc.reason)
        return False
    except OSError as exc:
        logger.warning("Host agent install error for %s: %s", service_id, exc)
        return False


def _call_agent_sync_config(service_id: str) -> bool:
    """Ask host agent to copy <ext>/config/* into INSTALL_DIR/config/.

    The dashboard-api container has /ods/config bind-mounted
    read-only, so it cannot do this work itself. The host agent runs
    on the writable host filesystem.

    Returns True on success (including the no-op case where the
    extension has no shipped config), False if the agent rejected or
    was unreachable.
    """
    url = f"{AGENT_URL}/v1/extension/sync_config"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ODS_AGENT_KEY}",
    }
    data = json.dumps({"service_id": service_id}).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_AGENT_TIMEOUT) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        logger.warning(
            "sync_config failed for %s (HTTP %d)", service_id, exc.code,
        )
        return False
    except (urllib.error.URLError, OSError, TimeoutError):
        logger.warning(
            "Host agent unreachable for sync_config at %s", AGENT_URL,
        )
        return False


def _call_agent_compose_rename(action: str, service_id: str) -> bool:
    """Ask host agent to rename compose.yaml <-> compose.yaml.disabled.

    Used for built-in extensions where the extensions mount is read-only.
    action must be 'activate' or 'deactivate'.
    """
    url = f"{AGENT_URL}/v1/extension/{action}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ODS_AGENT_KEY}",
    }
    data = json.dumps({"service_id": service_id}).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_AGENT_LOG_TIMEOUT) as resp:
            return resp.status == 200
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc:
        logger.warning(
            "Host agent unreachable for compose rename at %s: %s", AGENT_URL, exc,
        )
        return False


_agent_cache_lock = threading.Lock()
_agent_cache = {"available": False, "checked_at": 0.0}


def _check_agent_health() -> bool:
    """Check if host agent is available. Cached for 30s, thread-safe."""
    with _agent_cache_lock:
        now = time.monotonic()
        if now - _agent_cache["checked_at"] < 30:
            return _agent_cache["available"]
    # Check outside lock to avoid holding it during network I/O
    try:
        req = urllib.request.Request(f"{AGENT_URL}/health")
        with urllib.request.urlopen(req, timeout=3) as resp:
            available = resp.status == 200
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
        available = False
    with _agent_cache_lock:
        _agent_cache.update(available=available, checked_at=time.monotonic())
    return available


@contextlib.contextmanager
def _extensions_lock():
    """Acquire an exclusive file lock for extension mutations."""
    lock_path = _extensions_lock_path()
    lockfile = open(lock_path, "a+b")
    try:
        if fcntl is not None:
            fcntl.flock(lockfile, fcntl.LOCK_EX)
        elif msvcrt is not None:
            lockfile.seek(0, os.SEEK_END)
            if lockfile.tell() == 0:
                lockfile.write(b"\0")
                lockfile.flush()
            lockfile.seek(0)
            msvcrt.locking(lockfile.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(lockfile, fcntl.LOCK_UN)
            elif msvcrt is not None:
                lockfile.seek(0)
                msvcrt.locking(lockfile.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            lockfile.close()


def _extensions_lock_candidates() -> list[Path]:
    data_path = Path(DATA_DIR)
    return [
        data_path / ".extensions-lock",
        data_path / "config" / ".extensions-lock",
    ]


def _extensions_lock_path() -> Path:
    last_error: OSError | None = None
    for lock_path in _extensions_lock_candidates():
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.touch(exist_ok=True)
            if lock_path != Path(DATA_DIR) / ".extensions-lock":
                logger.warning("extensions lock falling back to %s", lock_path)
            return lock_path
        except OSError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


@router.get("/api/extensions/catalog")
async def extensions_catalog(
    category: Optional[str] = None,
    gpu_compatible: Optional[bool] = None,
    api_key: str = Depends(verify_api_key),
):
    """Get the extensions catalog with computed status."""
    _cleanup_future = asyncio.get_running_loop().run_in_executor(
        None, _cleanup_stale_progress,
    )

    def _log_cleanup_error(f: asyncio.Future) -> None:
        exc = f.exception()
        if exc is not None:
            logger.error("stale-progress cleanup failed: %s", exc, exc_info=exc)

    _cleanup_future.add_done_callback(_log_cleanup_error)

    from helpers import get_cached_services, get_all_services

    service_list = get_cached_services()
    if service_list is None:
        service_list = await get_all_services()
    services_by_id = {s.id: s for s in service_list}

    # Health-check user extensions so _compute_extension_status can distinguish
    # "enabled" (healthy) from "stopped" (unhealthy / not running).
    from helpers import _CATALOG_HEALTH_TIMEOUT, check_service_health
    from user_extensions import get_user_services_cached

    user_svc_configs = await asyncio.to_thread(get_user_services_cached, USER_EXTENSIONS_DIR)

    # Only health-check extensions that declare a health endpoint.  Use a
    # short per-probe timeout so one slow extension cannot stall the catalog
    # response (frontend aborts at 8 s).
    checkable = {sid: cfg for sid, cfg in user_svc_configs.items() if cfg.get("health")}
    user_health_tasks = [
        check_service_health(sid, cfg, timeout=_CATALOG_HEALTH_TIMEOUT)
        for sid, cfg in checkable.items()
    ]
    user_health = await asyncio.gather(*user_health_tasks, return_exceptions=True)
    for (sid, _), result in zip(checkable.items(), user_health):
        if not isinstance(result, BaseException):
            services_by_id[sid] = result

    # Extensions without health endpoints — assume running if scanned
    # (presence in user_svc_configs means compose.yaml + manifest exist)
    from models import ServiceStatus
    for sid, cfg in user_svc_configs.items():
        if not cfg.get("health") and sid not in services_by_id:
            services_by_id[sid] = ServiceStatus(
                id=sid, name=cfg.get("name", sid),
                port=cfg.get("port", 0),
                external_port=cfg.get("external_port", cfg.get("port", 0)),
                status="healthy", response_time_ms=None,
            )

    extensions = []
    for ext in EXTENSION_CATALOG:
        status = _compute_extension_status(ext, services_by_id)
        installable = _is_installable(ext["id"])
        ext_id = ext["id"]
        user_dir = USER_EXTENSIONS_DIR / ext_id
        source = "user" if user_dir.is_dir() else ("core" if ext_id in SERVICES else "library")
        has_data = (Path(DATA_DIR) / ext_id).is_dir()
        enriched = {
            **ext,
            "status": status,
            "installable": installable,
            "source": source,
            "has_data": has_data,
            "depends_on": ext.get("depends_on", []),
            "dependents": [],
            "dependency_status": {},
        }
        # Surface install-failure reason inline. The progress file already
        # records `error` (set by _write_error_progress) but it lives behind
        # a separate /progress endpoint, so a caller seeing `status: "error"`
        # in the catalog has no idea why without a second round-trip.
        if status == "error":
            _progress = _read_progress(ext_id)
            if _progress and _progress.get("error"):
                enriched["error_message"] = _progress["error"]

        if category and ext.get("category") != category:
            continue
        if gpu_compatible is not None:
            is_compatible = status != "incompatible"
            if gpu_compatible != is_compatible:
                continue

        extensions.append(enriched)

    # Compute reverse dependency map and dependency status
    dep_map: dict[str, list[str]] = {}
    for e in extensions:
        for dep in e.get("depends_on", []):
            dep_map.setdefault(dep, []).append(e["id"])
    ext_by_id = {e["id"]: e for e in extensions}
    for e in extensions:
        e["dependents"] = dep_map.get(e["id"], [])
        dep_status = {}
        for dep in e.get("depends_on", []):
            dep_ext = ext_by_id.get(dep)
            if dep_ext:
                dep_status[dep] = dep_ext["status"]
            elif dep in SERVICES:
                svc = services_by_id.get(dep)
                dep_status[dep] = "enabled" if (svc and svc.status == "healthy") else "disabled"
            else:
                dep_status[dep] = "unknown"
        e["dependency_status"] = dep_status

    summary = {
        "total": len(extensions),
        "installed": sum(1 for e in extensions if e["status"] in ("enabled", "cli_installed", "disabled", "stopped", "unhealthy")),
        "enabled": sum(1 for e in extensions if e["status"] == "enabled"),
        "cli_installed": sum(1 for e in extensions if e["status"] == "cli_installed"),
        "disabled": sum(1 for e in extensions if e["status"] == "disabled"),
        "stopped": sum(1 for e in extensions if e["status"] == "stopped"),
        "unhealthy": sum(1 for e in extensions if e["status"] == "unhealthy"),
        "installing": sum(1 for e in extensions if e["status"] == "installing"),
        "setting_up": sum(1 for e in extensions if e["status"] == "setting_up"),
        "error": sum(1 for e in extensions if e["status"] == "error"),
        "not_installed": sum(1 for e in extensions if e["status"] == "not_installed"),
        "incompatible": sum(1 for e in extensions if e["status"] == "incompatible"),
    }

    try:
        lib_available = (
            EXTENSIONS_LIBRARY_DIR.is_dir()
            and any(EXTENSIONS_LIBRARY_DIR.iterdir())
        )
    except OSError:
        lib_available = False

    agent_available = await asyncio.to_thread(_check_agent_health)

    return {
        "extensions": extensions,
        "summary": summary,
        "gpu_backend": GPU_BACKEND,
        "library_available": lib_available,
        "agent_available": agent_available,
    }


@router.get("/api/extensions/{service_id}/progress")
def extension_progress(service_id: str, api_key: str = Depends(verify_api_key)):
    """Get install progress for an extension."""
    _validate_service_id(service_id)
    progress_file = Path(DATA_DIR) / "extension-progress" / f"{service_id}.json"
    if not progress_file.exists():
        return {"service_id": service_id, "status": "idle"}
    try:
        data = json.loads(progress_file.read_text(encoding="utf-8"))
        return data
    except json.JSONDecodeError:
        return {"service_id": service_id, "status": "idle"}
    except OSError as exc:
        logger.warning("Failed to read progress file for %s: %s", service_id, exc)
        return {"service_id": service_id, "status": "idle"}


@router.get("/api/extensions/{service_id}")
async def extension_detail(
    service_id: str,
    api_key: str = Depends(verify_api_key),
):
    """Get detailed information for a single extension."""
    if not _SERVICE_ID_RE.match(service_id):
        raise HTTPException(status_code=404, detail=f"Invalid service_id: {service_id}")

    ext = next((e for e in EXTENSION_CATALOG if e["id"] == service_id), None)
    if not ext:
        raise HTTPException(status_code=404, detail=f"Extension not found: {service_id}")

    from helpers import _CATALOG_HEALTH_TIMEOUT, check_service_health, get_all_services
    from user_extensions import get_user_services_cached

    service_list = await get_all_services()
    services_by_id = {s.id: s for s in service_list}

    user_svc_configs = await asyncio.to_thread(get_user_services_cached, USER_EXTENSIONS_DIR)

    # Same short per-probe timeout as the catalog fan-out — one slow user
    # extension must not block the detail view.
    checkable = {sid: cfg for sid, cfg in user_svc_configs.items() if cfg.get("health")}
    user_health_tasks = [
        check_service_health(sid, cfg, timeout=_CATALOG_HEALTH_TIMEOUT)
        for sid, cfg in checkable.items()
    ]
    user_health = await asyncio.gather(*user_health_tasks, return_exceptions=True)
    for (sid, _), result in zip(checkable.items(), user_health):
        if not isinstance(result, BaseException):
            services_by_id[sid] = result

    from models import ServiceStatus
    for sid, cfg in user_svc_configs.items():
        if not cfg.get("health") and sid not in services_by_id:
            services_by_id[sid] = ServiceStatus(
                id=sid, name=cfg.get("name", sid),
                port=cfg.get("port", 0),
                external_port=cfg.get("external_port", cfg.get("port", 0)),
                status="healthy", response_time_ms=None,
            )

    status = _compute_extension_status(ext, services_by_id)
    installable = _is_installable(service_id)

    user_dir = USER_EXTENSIONS_DIR / service_id
    source = "user" if user_dir.is_dir() else ("core" if service_id in SERVICES else "library")

    # See extensions_catalog: same rationale for inlining the install error.
    error_message: Optional[str] = None
    if status == "error":
        _progress = _read_progress(service_id)
        if _progress and _progress.get("error"):
            error_message = _progress["error"]

    return {
        "id": ext["id"],
        "name": ext["name"],
        "description": ext.get("description", ""),
        "status": status,
        "error_message": error_message,
        "source": source,
        "installable": installable,
        "manifest": ext,
        "env_vars": ext.get("env_vars", []),
        "features": ext.get("features", []),
        "setup_instructions": {
            "steps": [
                f"Run 'ods enable {service_id}' to install and start the service",
                f"Run 'ods disable {service_id}' to stop the service",
            ],
            "cli_enable": f"ods enable {service_id}",
            "cli_disable": f"ods disable {service_id}",
        },
    }


# --- Mutation endpoints ---


@router.post("/api/extensions/{service_id}/logs")
async def extension_logs(
    service_id: str,
    api_key: str = Depends(verify_api_key),
):
    """Get container logs for any service via the host agent."""
    if not _SERVICE_ID_RE.match(service_id):
        raise HTTPException(status_code=404, detail=f"Invalid service_id: {service_id}")

    url = f"{AGENT_URL}/v1/service/logs"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ODS_AGENT_KEY}",
    }
    data = json.dumps({"service_id": service_id, "tail": 100}).encode()
    try:
        body = await asyncio.to_thread(
            _fetch_agent_logs, url, headers, data, _AGENT_LOG_TIMEOUT,
        )
        return json.loads(body)
    except urllib.error.HTTPError as exc:
        try:
            err_body = json.loads(exc.read().decode())
            detail = err_body.get("error", f"Host agent error: HTTP {exc.code}")
        except (json.JSONDecodeError, OSError):
            detail = f"Host agent returned HTTP {exc.code}"
        raise HTTPException(status_code=502, detail=detail)
    except (urllib.error.URLError, OSError):
        raise HTTPException(
            status_code=503,
            detail=f"Host agent unavailable. Use 'docker logs ods-{service_id}' in terminal.",
        )


def _install_from_library(service_id: str) -> None:
    """Copy an extension from the library to USER_EXTENSIONS_DIR atomically.

    Must be called inside _extensions_lock() by the caller. Performs the
    library path check, size check, and atomic stage+rename. Does NOT call
    hooks or start the container — that's the caller's responsibility.

    Raises HTTPException on failure.
    """
    # Verify library is accessible
    try:
        lib_available = EXTENSIONS_LIBRARY_DIR.is_dir()
    except OSError:
        lib_available = False
    if not lib_available:
        raise HTTPException(
            status_code=503, detail="Extensions library is unavailable",
        )

    source = (EXTENSIONS_LIBRARY_DIR / service_id).resolve()
    if not source.is_relative_to(EXTENSIONS_LIBRARY_DIR.resolve()):
        raise HTTPException(
            status_code=404, detail=f"Extension not found: {service_id}",
        )
    if not source.is_dir():
        raise HTTPException(
            status_code=404, detail=f"Extension not found: {service_id}",
        )

    # Server-side install gate: refuse entries that have no deployable
    # compose.yaml on disk (entries shipping only compose.yaml.disabled or
    # compose.yaml.reference, e.g. dify, jan, fooocus). The catalog/UI hides
    # the Install button for these via _is_installable, but a direct
    # POST /api/extensions/{id}/install would otherwise succeed-without-effect:
    # the directory gets copied to user-extensions/ but the host agent has
    # nothing to start, surfacing as a cryptic post-install failure.
    if not (source / "compose.yaml").exists():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Extension '{service_id}' has no deployable compose.yaml "
                f"and is not installable. Library entries that ship only "
                f"compose.yaml.disabled or compose.yaml.reference files are "
                f"reference material, not deployable services."
            ),
        )

    dest = USER_EXTENSIONS_DIR / service_id

    # Re-check under lock to prevent double-install race
    if dest.exists():
        has_compose = (dest / "compose.yaml").exists()
        has_disabled = (dest / "compose.yaml.disabled").exists()
        if (has_compose or has_disabled) and not _has_error_progress(service_id):
            raise HTTPException(
                status_code=409,
                detail=f"Extension already installed: {service_id}",
            )
        # Broken or failed directory — clean up before reinstall.
        logger.warning("Cleaning up extension directory under lock before retry: %s", dest)
        shutil.rmtree(dest)
        _clear_progress(service_id)

    # Size check
    total_size = 0
    for root, _dirs, files in os.walk(source):
        for f in files:
            total_size += os.path.getsize(os.path.join(root, f))
            if total_size > _MAX_EXTENSION_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail="Extension exceeds maximum size of 50MB",
                )

    USER_EXTENSIONS_DIR.mkdir(parents=True, exist_ok=True)
    tmp_parent = USER_EXTENSIONS_DIR / ".tmp"
    tmp_parent.mkdir(parents=True, exist_ok=True)
    tmpdir = tempfile.mkdtemp(prefix=f".{service_id}-", dir=str(tmp_parent))
    try:
        staged = Path(tmpdir) / service_id
        _copytree_safe(source, staged)
        # Security scan the staged copy (prevents TOCTOU)
        staged_compose = staged / "compose.yaml"
        if staged_compose.exists():
            _scan_compose_content(staged_compose, trusted=True)
            # Rewrite build.context to an absolute path under the final
            # extension dir.
            # Compose resolves relative contexts against the project dir
            # (INSTALL_DIR), not the extension dir, so "context: ." would
            # look for the Dockerfile in INSTALL_DIR/Dockerfile and fail.
            _rewrite_build_context(staged_compose, dest.resolve())
        os.rename(str(staged), str(dest))
    finally:
        if Path(tmpdir).exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


def _rewrite_build_context(compose_path: Path, final_dir: Path) -> None:
    """Rewrite build.context in a staged compose.yaml to an absolute path.

    Library extensions ship `build: { context: ., ... }` so they're portable
    inside the library, but `docker compose` resolves relative contexts
    against the compose project directory (INSTALL_DIR), not the extension's
    own directory. After staging, rewrite each service's relative
    build.context to the matching absolute path under the final post-rename
    extension directory.

    Idempotent: absolute paths are left alone (in case the compose was
    already rewritten on a previous attempt).
    """
    with open(compose_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        return

    services = data.get("services")
    if not isinstance(services, dict):
        return

    final_dir = final_dir.resolve()
    changed = False

    def is_absolute_context(context: str) -> bool:
        return os.path.isabs(context) or context.startswith("/")

    def resolve_relative_context(service_name: str, context: str) -> str:
        rewritten = (final_dir / context).resolve()
        if not rewritten.is_relative_to(final_dir):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Service '{service_name}' build.context escapes the "
                    "extension directory"
                ),
            )
        return str(rewritten)

    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        build = service.get("build")
        if build is None:
            continue

        if isinstance(build, str):
            # Short-form `build: <path>` — normalize to dict form
            if is_absolute_context(build):
                continue
            rewritten_context = resolve_relative_context(service_name, build)
            service["build"] = {"context": rewritten_context}
            logger.info(
                "Rewrote build context for service '%s' from '%s' to '%s'",
                service_name, build, rewritten_context,
            )
            changed = True
            continue

        if isinstance(build, dict):
            context = build.get("context")
            if context is None:
                # Compose default is `.`; make it explicit and absolute
                build["context"] = str(final_dir)
                logger.info(
                    "Set build context for service '%s' to '%s' (was implicit)",
                    service_name, final_dir,
                )
                changed = True
                continue
            if isinstance(context, str) and not is_absolute_context(context):
                rewritten_context = resolve_relative_context(service_name, context)
                build["context"] = rewritten_context
                logger.info(
                    "Rewrote build context for service '%s' from '%s' to '%s'",
                    service_name, context, rewritten_context,
                )
                changed = True

    if changed:
        with open(compose_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)


@router.post("/api/extensions/{service_id}/install")
def install_extension(service_id: str, api_key: str = Depends(verify_api_key)):
    """Install an extension from the library."""
    _validate_service_id(service_id)
    _assert_not_core(service_id)

    dest = USER_EXTENSIONS_DIR / service_id

    # Early check (non-authoritative, rechecked under lock in _install_from_library)
    if dest.exists():
        has_compose = (dest / "compose.yaml").exists()
        has_disabled = (dest / "compose.yaml.disabled").exists()
        if (has_compose or has_disabled) and not _has_error_progress(service_id):
            raise HTTPException(
                status_code=409, detail=f"Extension already installed: {service_id}",
            )
        # Broken or failed directory — clean up before reinstall.
        logger.warning("Cleaning up extension directory before retry: %s", dest)
        shutil.rmtree(dest)
        _clear_progress(service_id)

    # NOTE: pre_install hook is deferred to a future version. On fresh library
    # installs, the extension directory doesn't exist yet, so the host agent
    # cannot resolve the hook script. The call site is intentionally omitted
    # until the install flow can read manifests from the library source.

    # Atomic install via shared helper (used by templates too)
    with _extensions_lock():
        _install_from_library(service_id)
        _call_agent_invalidate_compose_cache()

    # Sync config/ subdirectory to INSTALL_DIR/config/ for bind mounts.
    # Some extensions (continue, sillytavern) ship a config/<id>/ directory
    # that the compose.yaml bind-mounts relative to the compose project root
    # (INSTALL_DIR), not relative to the extension directory.
    _sync_extension_config(service_id)

    # Write initial progress file so status shows "installing" immediately
    # (before host agent starts processing — closes the race window)
    _write_initial_progress(service_id)

    # Call host agent combined install (setup_hook → pull → start).
    # The setup_hook step internally satisfies the post_install lifecycle
    # contract — _resolve_hook("post_install") falls back to manifest's
    # setup_hook field, so we don't double-run it here.
    agent_ok = _call_agent_install(service_id)

    if not agent_ok:
        _write_error_progress(
            service_id,
            "Host agent failed to start extension. Run 'ods restart' to recover.",
        )

    logger.info("Installed extension: %s", service_id)
    return {
        "id": service_id,
        "action": "installed",
        "restart_required": not agent_ok,
        "progress_endpoint": f"/api/extensions/{service_id}/progress",
        "message": (
            "Extension installed and starting." if agent_ok
            else "Extension installed. Run 'ods restart' to start."
        ),
    }


def _read_direct_deps(service_id: str) -> list[str]:
    """Return direct depends_on list for a service from its manifest."""
    ext_dir = USER_EXTENSIONS_DIR / service_id
    manifest_path = None
    for name in ("manifest.yaml", "manifest.yml"):
        candidate = ext_dir / name
        if candidate.exists():
            manifest_path = candidate
            break
    if manifest_path is None:
        return []
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return []
    if not isinstance(manifest, dict):
        return []
    svc = manifest.get("service", {})
    depends_on = svc.get("depends_on", []) if isinstance(svc, dict) else []
    if not isinstance(depends_on, list):
        return []
    return [d for d in depends_on if isinstance(d, str) and _SERVICE_ID_RE.match(d)]


def _is_dep_satisfied(dep: str) -> bool:
    """Check if a dependency is already enabled (always-on, built-in, or user)."""
    if dep in ALWAYS_ON_SERVICES:
        return True
    if (EXTENSIONS_DIR / dep / "compose.yaml").exists():
        return True
    if (USER_EXTENSIONS_DIR / dep / "compose.yaml").exists():
        return True
    return False


def _get_missing_deps_transitive(
    service_id: str, *, _visiting: set | None = None, _order: list | None = None,
) -> list[str]:
    """Return all transitive missing deps in dependency order (leaves first).

    Raises HTTPException on circular dependency.
    """
    if _visiting is None:
        _visiting = set()
    if _order is None:
        _order = []

    if service_id in _visiting:
        raise HTTPException(
            status_code=400,
            detail=f"Circular dependency detected involving: {service_id}",
        )
    _visiting.add(service_id)

    for dep in _read_direct_deps(service_id):
        if _is_dep_satisfied(dep):
            continue
        if dep in _order:
            continue  # already queued from another branch
        _get_missing_deps_transitive(dep, _visiting=_visiting, _order=_order)
        _order.append(dep)

    _visiting.discard(service_id)
    return _order


def _activate_service(service_id: str) -> dict:
    """Core enable logic — NO lock acquisition. Called inside _extensions_lock.

    Checks both USER_EXTENSIONS_DIR (user-installed) and EXTENSIONS_DIR
    (built-in) so templates can enable built-in extensions like n8n, tts, etc.

    Returns a result dict for the service. Cycle detection is handled
    upstream by _get_missing_deps_transitive.
    """
    ext_dir = _resolve_extension_dir(service_id)

    disabled_compose = ext_dir / "compose.yaml.disabled"
    enabled_compose = ext_dir / "compose.yaml"

    # Already enabled — skip silently (idempotent for dep chains)
    if enabled_compose.exists():
        return {"id": service_id, "action": "already_enabled"}

    if not disabled_compose.exists():
        raise HTTPException(
            status_code=404, detail=f"Extension has no compose file: {service_id}",
        )

    # Re-scan compose content (TOCTOU prevention). Built-in extensions
    # legitimately declare their own service name in their compose file, so
    # skip the CORE_SERVICE_IDS name-collision check for them. User extensions
    # still get the full anti-shadowing scan. Some built-ins also legitimately
    # need `user: "0:0"` to perform init-time chown before dropping privileges
    # via setpriv (e.g. openclaw), so skip the root-user check for built-ins
    # only. The `trusted` flag is separate and controls whether `build:`
    # directives are allowed (library installs need it, built-in activations
    # do not).
    is_builtin = ext_dir.is_relative_to(EXTENSIONS_DIR.resolve())
    _scan_compose_content(
        disabled_compose,
        skip_name_collision=is_builtin,
        skip_gpu_passthrough_check=is_builtin,
        skip_root_user_check=is_builtin,
    )

    # Reject symlinks
    st = os.lstat(disabled_compose)
    if stat.S_ISLNK(st.st_mode):
        raise HTTPException(
            status_code=400, detail="Compose file is a symlink",
        )

    # Built-in extensions live on a :ro mount — delegate rename to host agent
    if is_builtin:
        if not _call_agent_compose_rename("activate", service_id):
            raise HTTPException(
                status_code=502,
                detail=f"Host agent failed to activate extension: {service_id}",
            )
    else:
        os.rename(str(disabled_compose), str(enabled_compose))
    logger.info("Enabled extension (activate): %s", service_id)
    return {"id": service_id, "action": "enabled"}


@router.post("/api/extensions/{service_id}/enable")
def enable_extension(
    service_id: str,
    auto_enable_deps: bool = Query(False),
    api_key: str = Depends(verify_api_key),
):
    """Enable an installed extension, optionally auto-enabling dependencies."""
    _validate_service_id(service_id)
    _assert_not_core(service_id)

    ext_dir = _resolve_extension_dir(service_id)

    disabled_compose = ext_dir / "compose.yaml.disabled"
    enabled_compose = ext_dir / "compose.yaml"

    # Stopped case: compose.yaml exists but container is not running — just start it
    if enabled_compose.exists():
        with _extensions_lock():
            st = os.lstat(enabled_compose)
            if stat.S_ISLNK(st.st_mode):
                raise HTTPException(
                    status_code=400, detail="Compose file is a symlink",
                )
            # Built-in extensions legitimately use their own service name which
            # appears in CORE_SERVICE_IDS — skip the name-collision check for
            # them, mirroring _activate_service's logic.
            is_builtin = ext_dir.is_relative_to(EXTENSIONS_DIR.resolve())
            _scan_compose_content(
                enabled_compose,
                skip_name_collision=is_builtin,
                skip_gpu_passthrough_check=is_builtin,
                skip_root_user_check=is_builtin,
            )
        # Dependencies were satisfied at install time; compose content is re-scanned above
        _write_initial_progress(service_id)
        # Invalidate .compose-flags cache so ods-cli picks up this extension
        # before the host agent starts the container.
        _call_agent_invalidate_compose_cache()
        agent_ok = _call_agent("start", service_id)
        if not agent_ok:
            _write_error_progress(
                service_id,
                "Host agent failed to start extension. Run 'ods restart' to recover.",
            )
        logger.info("Started stopped extension: %s", service_id)
        return {
            "id": service_id,
            "action": "enabled",
            "restart_required": not agent_ok,
            "message": (
                "Extension started." if agent_ok
                else "Extension is enabled. Run 'ods restart' to start."
            ),
        }

    if not disabled_compose.exists():
        raise HTTPException(
            status_code=404, detail=f"Extension has no compose file: {service_id}",
        )

    # Check dependencies (transitive — gathers full tree, detects cycles)
    missing_deps = _get_missing_deps_transitive(service_id)
    if missing_deps and not auto_enable_deps:
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"Missing dependencies: {', '.join(missing_deps)}",
                "missing_dependencies": missing_deps,
                "auto_enable_available": True,
            },
        )

    enabled_services: list[str] = []

    with _extensions_lock():
        # Auto-enable missing deps first (already in dependency order — leaves first)
        if missing_deps and auto_enable_deps:
            for dep in missing_deps:
                _validate_service_id(dep)
                result = _activate_service(dep)
                if result.get("action") == "enabled":
                    enabled_services.append(dep)

        # Enable the target service
        result = _activate_service(service_id)
        if result.get("action") == "enabled":
            enabled_services.append(service_id)

        # Invalidate .compose-flags cache so ods-cli picks up the new enabled set
        if enabled_services:
            _call_agent_invalidate_compose_cache()

    # Start all enabled services via agent (outside lock)
    agent_ok = True
    warnings: list[str] = []
    for svc_id in enabled_services:
        # pre_start failure is terminal for this service — do not start it
        if not _call_agent_hook(svc_id, "pre_start"):
            agent_ok = False
            _write_error_progress(
                svc_id,
                "pre_start hook failed — extension not started.",
            )
            continue
        if not _call_agent("start", svc_id):
            agent_ok = False
        # post_start is non-terminal — log failure but don't fail the enable
        if not _call_agent_hook(svc_id, "post_start"):
            logger.warning("post_start hook failed for %s (non-fatal)", svc_id)
            warnings.append(
                f"{svc_id}: post_start hook failed — manual configuration may be needed",
            )

    logger.info("Enabled extension: %s (deps: %s)", service_id,
                enabled_services[:-1] if len(enabled_services) > 1 else "none")
    return {
        "id": service_id,
        "action": "enabled",
        "enabled_services": enabled_services,
        "restart_required": not agent_ok,
        "warnings": warnings,
        "message": (
            "Extension enabled and started." if agent_ok
            else "Extension enabled. Run 'ods restart' to start."
        ),
    }


@router.post("/api/extensions/{service_id}/disable")
def disable_extension(service_id: str, include_data_info: bool = Query(True), api_key: str = Depends(verify_api_key)):
    """Disable an enabled extension."""
    _validate_service_id(service_id)
    _assert_not_core(service_id)

    ext_dir = _resolve_extension_dir(service_id)

    enabled_compose = ext_dir / "compose.yaml"
    disabled_compose = ext_dir / "compose.yaml.disabled"

    if not enabled_compose.exists():
        raise HTTPException(
            status_code=409, detail=f"Extension already disabled: {service_id}",
        )

    # Check reverse dependents (warn, don't block)
    dependents_warning = []
    try:
        if USER_EXTENSIONS_DIR.is_dir():
            for peer_dir in USER_EXTENSIONS_DIR.iterdir():
                if not peer_dir.is_dir() or peer_dir.name == service_id:
                    continue
                peer_manifest = peer_dir / "manifest.yaml"
                if not peer_manifest.exists():
                    continue
                try:
                    peer_data = yaml.safe_load(
                        peer_manifest.read_text(encoding="utf-8"),
                    )
                    if isinstance(peer_data, dict):
                        peer_svc = peer_data.get("service", {})
                        deps = peer_svc.get("depends_on", []) if isinstance(peer_svc, dict) else []
                        if isinstance(deps, list) and service_id in deps:
                            dependents_warning.append(peer_dir.name)
                except (yaml.YAMLError, OSError) as e:
                    logger.debug("Could not read peer manifest %s: %s", peer_manifest, e)
    except OSError:
        pass

    # Call agent to stop BEFORE renaming (prevents zombie containers)
    agent_ok = _call_agent("stop", service_id)
    if not agent_ok:
        logger.warning("Could not stop %s via agent — container may still be running", service_id)

    with _extensions_lock():
        # lstat check inside lock (TOCTOU prevention)
        st = os.lstat(enabled_compose)
        if stat.S_ISLNK(st.st_mode):
            raise HTTPException(
                status_code=400, detail="Compose file is a symlink",
            )

        # Built-in extensions live on a :ro mount — delegate rename to host agent
        is_builtin = ext_dir.is_relative_to(EXTENSIONS_DIR.resolve())
        if is_builtin:
            if not _call_agent_compose_rename("deactivate", service_id):
                raise HTTPException(
                    status_code=502,
                    detail=f"Host agent failed to deactivate extension: {service_id}",
                )
        else:
            os.rename(str(enabled_compose), str(disabled_compose))
        _call_agent_invalidate_compose_cache()

        progress_file = Path(DATA_DIR) / "extension-progress" / f"{service_id}.json"
        progress_file.unlink(missing_ok=True)

    logger.info("Disabled extension: %s", service_id)

    message = (
        "Extension disabled and stopped." if agent_ok
        else "Extension disabled. Run 'ods restart' to apply changes."
    )
    if dependents_warning:
        message = (
            f"Warning: {', '.join(dependents_warning)} depend on {service_id}. "
            + message
        )

    return {
        "id": service_id,
        "action": "disabled",
        "restart_required": not agent_ok,
        "dependents_warning": dependents_warning,
        "data_info": _get_service_data_info(service_id) if include_data_info else None,
        "message": message,
    }


@router.delete("/api/extensions/{service_id}")
def uninstall_extension(service_id: str, include_data_info: bool = Query(True), api_key: str = Depends(verify_api_key)):
    """Uninstall a disabled extension."""
    _validate_service_id(service_id)
    _assert_not_core(service_id)

    ext_dir = (USER_EXTENSIONS_DIR / service_id).resolve()
    if not ext_dir.is_relative_to(USER_EXTENSIONS_DIR.resolve()):
        raise HTTPException(
            status_code=404, detail=f"Extension not found: {service_id}",
        )
    if not ext_dir.is_dir():
        raise HTTPException(
            status_code=404, detail=f"Extension not installed: {service_id}",
        )

    # Must be disabled before uninstall
    if (ext_dir / "compose.yaml").exists():
        raise HTTPException(
            status_code=400,
            detail=f"Disable extension before uninstalling. Run 'ods disable {service_id}' first.",
        )

    with _extensions_lock():
        # Reject symlinks (checked under lock to prevent TOCTOU)
        st = os.lstat(ext_dir)
        if stat.S_ISLNK(st.st_mode):
            raise HTTPException(
                status_code=400, detail="Extension directory is a symlink",
            )

        try:
            shutil.rmtree(ext_dir)
        except OSError as e:
            logger.error("Failed to remove extension %s: %s", service_id, e)
            raise HTTPException(status_code=500, detail=f"Failed to remove extension files: {e}")
        _call_agent_invalidate_compose_cache()

        progress_file = Path(DATA_DIR) / "extension-progress" / f"{service_id}.json"
        progress_file.unlink(missing_ok=True)

    logger.info("Uninstalled extension: %s", service_id)
    return {
        "id": service_id,
        "action": "uninstalled",
        "data_info": _get_service_data_info(service_id) if include_data_info else None,
        "message": "Extension uninstalled. Docker volumes may remain — run 'docker volume ls' to check.",
        "cleanup_hint": f"To remove orphaned volumes: docker volume ls --filter 'name={service_id}' -q | xargs docker volume rm",
    }


class PurgeRequest(BaseModel):
    confirm: bool = False


@router.delete("/api/extensions/{service_id}/data")
def purge_extension_data(
    service_id: str,
    body: PurgeRequest,
    api_key: str = Depends(verify_api_key),
):
    """Permanently delete service data directory."""
    if not _SERVICE_ID_RE.match(service_id):
        raise HTTPException(status_code=404, detail=f"Invalid service_id: {service_id}")

    if service_id in ALWAYS_ON_SERVICES:
        raise HTTPException(status_code=403, detail="Cannot purge always-on service data")

    with _extensions_lock():
        # Check if service is still enabled (built-in or user extension)
        for check_dir in [Path(EXTENSIONS_DIR) / service_id, USER_EXTENSIONS_DIR / service_id]:
            if (check_dir / "compose.yaml").exists():
                raise HTTPException(status_code=400, detail=f"{service_id} is still enabled. Disable it first.")

        data_path = (Path(DATA_DIR) / service_id).resolve()
        if not data_path.is_relative_to(Path(DATA_DIR).resolve()):
            raise HTTPException(status_code=400, detail="Invalid data path")

        if not data_path.is_dir():
            raise HTTPException(status_code=404, detail=f"No data directory found for {service_id}")

        if not body.confirm:
            raise HTTPException(status_code=400, detail="Confirmation required: set confirm=true")

        from helpers import dir_size_gb, invalidate_dir_size_cache  # noqa: PLC0415
        size_gb = dir_size_gb(data_path)

        shutil.rmtree(data_path, ignore_errors=True)
        invalidate_dir_size_cache(data_path)

        if data_path.exists():
            raise HTTPException(status_code=500, detail=f"Could not fully remove data/{service_id}. Some files may be owned by root.")

        # Also clean up the per-service install-progress file so
        # _compute_extension_status does not keep showing a stale "installing"
        # entry after the user purges an extension's data.
        progress_file = Path(DATA_DIR) / "extension-progress" / f"{service_id}.json"
        progress_file.unlink(missing_ok=True)

        return {"id": service_id, "action": "purged", "size_gb_freed": size_gb}


@router.get("/api/storage/orphaned")
def orphaned_storage(api_key: str = Depends(verify_api_key)):
    """Find data directories not belonging to any known service."""
    from helpers import dir_size_gb  # noqa: PLC0415

    data_path = Path(DATA_DIR)
    if not data_path.is_dir():
        return {"orphaned": [], "total_gb": 0}

    # Known system directories that are not service data.  Includes runtime
    # state created outside the installer: extension-progress (this router)
    # and config-backups (host agent's .env backup writer).
    system_dirs = {
        "models", "config", "user-extensions", "extensions-library",
        "extension-progress", "config-backups",
    }
    known_ids = set(SERVICES.keys()) | system_dirs

    orphaned = []
    total = 0.0
    for child in sorted(data_path.iterdir()):
        if not child.is_dir():
            continue
        if child.name in known_ids:
            continue
        size = dir_size_gb(child)
        orphaned.append({"name": child.name, "size_gb": size, "path": f"data/{child.name}"})
        total += size

    return {"orphaned": orphaned, "total_gb": round(total, 2)}
