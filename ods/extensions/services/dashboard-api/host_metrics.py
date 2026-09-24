"""Bounded, shared physical Mac telemetry bridge for container dashboards."""
import math
import platform
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
