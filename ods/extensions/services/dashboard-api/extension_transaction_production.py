"""Production-owned inputs for Assistant First extension proposals.

This module deliberately wires planning and configuration custody before it
wires lifecycle execution.  The HTTP capability probe must therefore continue
to advertise ``execution: false`` until the host-owned apply adapter is
installed by the next phase.
"""

from __future__ import annotations

import datetime
import json
import os
import platform
import re
import shutil
from pathlib import Path
from typing import Any

from assistant_first_planner import PlanningError, canonical_json_bytes
from assistant_first_secret_client import HostSecretCustodian
from config import (
    DATA_DIR,
    EXTENSION_CATALOG,
    EXTENSION_CATALOG_REVISION,
    EXTENSION_PLANNING_POLICY,
    EXTENSIONS_DIR,
    GPU_BACKEND,
    INSTALL_DIR,
    USER_EXTENSIONS_DIR,
)
from extension_planning_contract import computed_catalog_revision
from extension_transaction_configuration import TransactionConfigurationManager
from extension_transaction_runtime import TransactionRuntime
from extension_transactions import TransactionStore


_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PLATFORMS = {"linux": "linux", "darwin": "darwin", "windows": "windows"}
_ARCHITECTURES = {
    "amd64": "amd64",
    "x86_64": "amd64",
    "arm64": "arm64",
    "aarch64": "arm64",
}
_CONTAINER_RUNTIMES = frozenset({"docker", "podman", "none"})
_GPU_BACKENDS = frozenset({"amd", "nvidia", "apple", "cpu", "none"})
_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def production_runtime_enabled() -> bool:
    return (
        os.environ.get("ODS_ASSISTANT_TRANSACTIONS_ENABLED", "").strip().casefold()
        in _ENABLED_VALUES
    )


def _clone(value: Any) -> Any:
    return json.loads(canonical_json_bytes(value))


def production_catalog() -> tuple[list[dict[str, Any]], str]:
    """Return an immutable snapshot and verify the generated revision."""

    entries = _clone(EXTENSION_CATALOG)
    revision = computed_catalog_revision(entries)
    if not isinstance(EXTENSION_CATALOG_REVISION, str) or not _SHA256_RE.fullmatch(
        EXTENSION_CATALOG_REVISION
    ):
        raise PlanningError("extension-catalog-revision-unavailable")
    if revision != EXTENSION_CATALOG_REVISION:
        raise PlanningError("extension-catalog-revision-mismatch")
    return entries, revision


def production_policy() -> dict[str, Any]:
    return _clone(EXTENSION_PLANNING_POLICY)


def _installed_version(install_dir: Path) -> str:
    env_path = install_dir / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("ODS_VERSION="):
                value = line.split("=", 1)[1].strip().strip("\"'")
                if _SEMVER_RE.fullmatch(value):
                    return value
    except (OSError, UnicodeError):
        pass
    try:
        raw = (install_dir / ".version").read_text(encoding="utf-8").strip()
        if raw.startswith("{"):
            document = json.loads(raw)
            raw = document.get("version", "") if isinstance(document, dict) else ""
        if isinstance(raw, str) and _SEMVER_RE.fullmatch(raw):
            return raw
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return "0.0.0"


def _available_ram_bytes() -> int:
    try:
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                values[parts[0].rstrip(":")] = int(parts[1]) * 1024
        if values.get("MemAvailable", 0) > 0:
            return values["MemAvailable"]
    except (OSError, UnicodeError):
        pass
    return 0


def _gpu_availability() -> tuple[int, int]:
    try:
        from gpu import get_gpu_info

        info = get_gpu_info()
    except Exception:
        return 0, 0
    if info is None:
        return 0, 0
    total = max(0, int(getattr(info, "memory_total_mb", 0))) * 1024 * 1024
    used = max(0, int(getattr(info, "memory_used_mb", 0))) * 1024 * 1024
    count = max(1, int(getattr(info, "gpu_count", 1))) if total else 0
    return max(0, total - used), count


def _entry_definition(entry: dict[str, Any]) -> tuple[str, str]:
    planning = entry.get("planning")
    if not isinstance(planning, dict):
        return "0.0.0-legacy", ""
    version = planning.get("version")
    digest = planning.get("definitionSha256")
    return (
        version if isinstance(version, str) and version else "0.0.0-legacy",
        digest if isinstance(digest, str) else "",
    )


def _cached_statuses() -> dict[str, str]:
    try:
        from helpers import get_cached_services

        cached = get_cached_services()
    except Exception:
        return {}
    if not isinstance(cached, list):
        return {}
    result: dict[str, str] = {}
    for service in cached:
        service_id = getattr(service, "id", None)
        status = getattr(service, "status", None)
        if isinstance(service_id, str) and isinstance(status, str):
            result[service_id] = status
    return result


def _installed_status(
    service_id: str,
    user_dir: Path,
    builtin_dir: Path,
    data_dir: Path,
    cached_statuses: dict[str, str],
) -> str | None:
    root = user_dir / service_id
    if not root.is_dir():
        root = builtin_dir / service_id
    if not root.is_dir() or root.is_symlink():
        return None
    progress = data_dir / "extension-progress" / f"{service_id}.json"
    try:
        value = json.loads(progress.read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("status") == "error":
            return "error"
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    if (root / "compose.yaml.disabled").is_file() or (
        root / "compose.yml.disabled"
    ).is_file():
        return "disabled"
    if (root / "compose.yaml").is_file() or (root / "compose.yml").is_file():
        return {
            "healthy": "enabled",
            "degraded": "unhealthy",
            "unhealthy": "unhealthy",
            "down": "stopped",
            "unknown": "stopped",
            "not_deployed": "stopped",
        }.get(cached_statuses.get(service_id, ""), "stopped")
    return None


def _host_platform() -> str:
    value = _PLATFORMS.get(platform.system().casefold())
    if value is None:
        raise PlanningError("unsupported-host-platform")
    return value


def _host_architecture() -> str:
    value = _ARCHITECTURES.get(platform.machine().casefold())
    if value is None:
        raise PlanningError("unsupported-host-architecture")
    return value


def _container_runtime() -> str:
    value = os.environ.get("ODS_CONTAINER_RUNTIME", "docker").strip().casefold()
    if value not in _CONTAINER_RUNTIMES:
        raise PlanningError("unsupported-container-runtime")
    return value


def _gpu_backend() -> str:
    value = str(GPU_BACKEND or "none").strip().casefold()
    if value not in _GPU_BACKENDS:
        raise PlanningError("unsupported-gpu-backend")
    return value


def _disk_probe_path(data_dir: Path, install_dir: Path) -> Path:
    for candidate in (data_dir, install_dir, data_dir.parent, install_dir.parent):
        if candidate.exists():
            return candidate
    return Path.cwd()


def production_observed_state(
    *,
    install_dir: Path | None = None,
    data_dir: Path | None = None,
    user_extensions_dir: Path | None = None,
    builtin_extensions_dir: Path | None = None,
    catalog_entries: list[dict[str, Any]] | None = None,
    cached_statuses: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the planner's bounded host snapshot from owned local evidence."""

    install = install_dir or Path(INSTALL_DIR)
    data = data_dir or Path(DATA_DIR)
    user_root = user_extensions_dir or Path(USER_EXTENSIONS_DIR)
    builtin_root = builtin_extensions_dir or Path(EXTENSIONS_DIR)
    system = _host_platform()
    architecture = _host_architecture()
    runtime = _container_runtime()
    gpu_backend = _gpu_backend()
    driver = os.environ.get("ODS_GPU_DRIVER_VERSION", "").strip() or None
    if driver is not None and re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}", driver) is None:
        driver = None

    installed: list[dict[str, Any]] = []
    occupied: list[dict[str, Any]] = []
    reserved: list[dict[str, Any]] = []
    seen_ports: set[tuple[str, int]] = set()
    seen_resources: set[str] = set()
    entries = EXTENSION_CATALOG if catalog_entries is None else catalog_entries
    statuses = _cached_statuses() if cached_statuses is None else cached_statuses
    for entry in sorted(entries, key=lambda item: str(item.get("id", ""))):
        service_id = entry.get("id")
        if not isinstance(service_id, str):
            continue
        status = _installed_status(service_id, user_root, builtin_root, data, statuses)
        if status is None:
            continue
        version, definition = _entry_definition(entry)
        if not _DIGEST_RE.fullmatch(definition):
            continue
        installed.append(
            {
                "id": service_id,
                "version": version,
                "definitionSha256": definition,
                "status": status,
            }
        )
        planning = entry.get("planning") if isinstance(entry.get("planning"), dict) else {}
        resources = planning.get("resources") if isinstance(planning.get("resources"), dict) else {}
        if status != "disabled":
            for port in resources.get("hostPorts", []):
                if type(port) is int and 1 <= port <= 65535 and ("tcp", port) not in seen_ports:
                    seen_ports.add(("tcp", port))
                    occupied.append({"port": port, "protocol": "tcp", "owner": service_id})
            for name in resources.get("exclusive", []):
                if isinstance(name, str) and name and name not in seen_resources:
                    seen_resources.add(name)
                    reserved.append({"name": name, "owner": service_id})

    try:
        disk_bytes = max(0, int(shutil.disk_usage(_disk_probe_path(data, install)).free))
    except OSError:
        disk_bytes = 0
    vram_bytes, gpu_count = _gpu_availability()
    return {
        "odsVersion": _installed_version(install),
        "platform": system,
        "architecture": architecture,
        "containerRuntime": runtime,
        "gpuBackend": gpu_backend,
        "driverVersion": driver,
        "available": {
            "diskBytes": disk_bytes,
            "ramBytes": _available_ram_bytes(),
            "vramBytes": vram_bytes,
            "cpuMillicores": max(0, int(os.cpu_count() or 0) * 1000),
            "gpuCount": gpu_count,
        },
        "occupiedPorts": occupied,
        "reservedResources": reserved,
        "installedServices": installed,
    }


def create_production_runtime() -> TransactionRuntime:
    """Create the production proposal/configuration runtime without execution."""

    root = Path(DATA_DIR) / "assistant-first" / "transaction-store"
    store = TransactionStore(root)
    configuration = TransactionConfigurationManager(store, HostSecretCustodian(), utc_now)
    return TransactionRuntime(
        store=store,
        catalog=production_catalog,
        observed_state=production_observed_state,
        policy=production_policy,
        clock=utc_now,
        executor=None,
        configuration=configuration,
    )


__all__ = [
    "create_production_runtime",
    "production_catalog",
    "production_observed_state",
    "production_policy",
    "production_runtime_enabled",
    "utc_now",
]
