"""Bounded, shared physical Mac telemetry bridge for container dashboards."""
import math
from datetime import datetime, timezone
import platform
import re
import threading
import time

from host_agent_client import AgentClientError, request_json

_lock = threading.Lock()
_cached = (0.0, None)


def finite(value, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
        return None
    return value


def linux_scope():
    release = platform.release().lower()
    if "microsoft" in release or "wsl" in release:
        return "wsl"
    if any(name in release for name in ("linuxkit", "lima", "colima")):
        return "vm"
    # /proc/stat and /proc/meminfo describe the Linux kernel, even when read
    # through a container. They are not cgroup/container allocation counters.
    return "host"


def apple_host_metrics():
    global _cached
    with _lock:
        if _cached[1] is not None and time.monotonic() - _cached[0] < 3:
            return _cached[1]
        cpu = {"percent": None, "temp_c": None, "scope": "host", "source": "macos-top"}
        ram = {"used_gb": None, "total_gb": None, "percent": None,
               "scope": "host", "source": "macos-vm-stat"}
        result = {"cpu": cpu, "ram": ram, "gpu": None}
        try:
            payload = request_json("GET", "/v1/system/metrics", timeout=5)
        except AgentClientError:
            payload = None
        if (isinstance(payload, dict) and payload.get("schema_version") == "ods.host-system-metrics.v1"
                and payload.get("platform") == "Darwin"):
            raw_cpu, raw_ram, raw_gpu = (payload.get(key) for key in ("cpu", "ram", "gpu"))
            if isinstance(raw_cpu, dict) and raw_cpu.get("scope") == "host":
                cpu["percent"] = finite(raw_cpu.get("percent"), maximum=100)
            if isinstance(raw_ram, dict) and raw_ram.get("scope") == "host":
                total = finite(raw_ram.get("total_gb"), minimum=0.1)
                used = finite(raw_ram.get("used_gb"), maximum=total) if total is not None else None
                ram.update(total_gb=total, used_gb=used,
                           percent=finite(raw_ram.get("percent"), maximum=100) if used is not None else None)
            if isinstance(raw_gpu, dict):
                total = finite(raw_gpu.get("memory_total_mb"), minimum=1)
                if total is not None:
                    name = raw_gpu.get("name")
                    result["gpu"] = {
                        "name": name[:128] if isinstance(name, str) and name else "Apple Silicon",
                        "memory_total_mb": int(total),
                        "memory_used_mb": finite(raw_gpu.get("memory_used_mb"), maximum=total),
                        "utilization_percent": finite(raw_gpu.get("utilization_percent"), maximum=100),
                    }
        _cached = (time.monotonic(), result)
        return result


_windows_cached = (0.0, None)


def windows_host_metrics():
    """Native Windows snapshot from an authenticated WSL host agent, if available."""
    global _windows_cached
    with _lock:
        if _windows_cached[1] is not None and time.monotonic() - _windows_cached[0] < 3:
            return _windows_cached[1]
        result = {"cpu": None, "ram": None, "gpus": []}
        try:
            payload = request_json("GET", "/v1/system/metrics", timeout=9)
        except AgentClientError:
            payload = None
        if (isinstance(payload, dict) and payload.get("schema_version") == "ods.host-system-metrics.v1"
                and payload.get("platform") == "Windows"):
            sampled = payload.get("sampledAt")
            # No sensor snapshot without its source time is advertised as current.
            try:
                source_time = datetime.fromisoformat(sampled) if isinstance(sampled, str) else None
                age = (datetime.now(timezone.utc) - source_time).total_seconds() if source_time and source_time.tzinfo else None
            except (ValueError, TypeError, OverflowError):
                age = None
            if age is not None and -5 <= age <= 30:
                for key, fields in (("cpu", ("percent",)), ("ram", ("used_gb", "total_gb", "percent"))):
                    raw = payload.get(key)
                    if not isinstance(raw, dict) or raw.get("scope") != "host":
                        continue
                    values = {field: finite(raw.get(field), maximum=100 if field == "percent" else None) for field in fields}
                    if key == "ram" and (not values["total_gb"] or values["used_gb"] is None or values["used_gb"] > values["total_gb"]):
                        continue
                    if any(value is None for value in values.values()):
                        continue
                    result[key] = {**values, "scope": "host", "source": "windows-cim", "sampledAt": sampled}
                    if key == "cpu":
                        result[key]["temp_c"] = None
                rows = payload.get("gpus")
                if isinstance(rows, list):
                    for row in rows[:32]:
                        if not isinstance(row, dict):
                            continue
                        total = finite(row.get("memory_total_mb"), minimum=1)
                        name, identity = row.get("name"), row.get("uuid")
                        if (total is None or not isinstance(name, str) or not name or not isinstance(identity, str)
                                or not re.fullmatch(r"luid_0x[0-9a-f]{8}_0x[0-9a-f]{8}", identity)
                                or row.get("memory_scope") != "dedicated"):
                            continue
                        result["gpus"].append({
                            "name": name[:128], "uuid": identity, "memory_total_mb": int(total),
                            "memory_used_mb": finite(row.get("memory_used_mb"), maximum=total),
                            "memory_type": "unified" if row.get("memory_type") == "unified" else "discrete",
                            "utilization_percent": finite(row.get("utilization_percent"), maximum=100),
                            "backend": row.get("backend") if row.get("backend") in ("amd", "nvidia") else "unknown",
                        })
        _windows_cached = (time.monotonic(), result)
        return result
