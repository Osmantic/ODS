"""GPU detection and metrics for NVIDIA, AMD, and Apple Silicon GPUs."""

import base64
import json as _json
import logging
import os
import platform
import subprocess
from pathlib import Path
from typing import Optional

from models import GPUInfo, IndividualGPU
from host_agent_client import AgentClientError, request_json as request_agent_json

logger = logging.getLogger(__name__)


def run_command(cmd: list[str], timeout: int = 5) -> tuple[bool, str]:
    """Run a shell command and return (success, output)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except (subprocess.SubprocessError, OSError) as e:
        return False, str(e)


def _read_sysfs(path: str) -> Optional[str]:
    """Read a sysfs file, returning None on failure."""
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except (OSError, IOError):
        return None


def _read_meminfo_mb() -> Optional[tuple[int, int]]:
    """Return (used_mb, total_mb) from /proc/meminfo, or None on failure.

    Used as a fallback for unified-memory NVIDIA GPUs (GB10, GB200) where
    nvidia-smi reports [N/A] for memory.total/memory.used. Mirrors the
    installer's detection.sh fallback.
    """
    try:
        with open("/proc/meminfo") as f:
            info = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
    except OSError:
        return None
    total_kb = info.get("MemTotal", 0)
    avail_kb = info.get("MemAvailable", 0)
    if total_kb <= 0:
        return None
    used_mb = max(0, (total_kb - avail_kb)) // 1024
    total_mb = total_kb // 1024
    return used_mb, total_mb


def _find_amd_gpu_sysfs() -> Optional[str]:
    """Find the sysfs base path for an AMD GPU device."""
    import glob
    for card_dir in sorted(glob.glob("/sys/class/drm/card*/device")):
        vendor = _read_sysfs(f"{card_dir}/vendor")
        if vendor == "0x1002":
            return card_dir
    return None


def _find_hwmon_dir(device_path: str) -> Optional[str]:
    """Find the hwmon directory for an AMD GPU device."""
    import glob
    hwmon_dirs = sorted(glob.glob(f"{device_path}/hwmon/hwmon*"))
    return hwmon_dirs[0] if hwmon_dirs else None


def get_gpu_info_amd() -> Optional[GPUInfo]:
    """Get GPU metrics from amdgpu sysfs."""
    base = _find_amd_gpu_sysfs()
    if not base:
        return None

    hwmon = _find_hwmon_dir(base)

    try:
        vram_total_str = _read_sysfs(f"{base}/mem_info_vram_total")
        vram_used_str = _read_sysfs(f"{base}/mem_info_vram_used")
        gtt_total_str = _read_sysfs(f"{base}/mem_info_gtt_total")
        gtt_used_str = _read_sysfs(f"{base}/mem_info_gtt_used")
        gpu_busy_str = _read_sysfs(f"{base}/gpu_busy_percent")

        if not vram_total_str or not vram_used_str:
            return None

        vram_total = int(vram_total_str)
        vram_used = int(vram_used_str)
        gtt_total = int(gtt_total_str) if gtt_total_str else 0
        gtt_used = int(gtt_used_str) if gtt_used_str else 0
        gpu_busy = int(gpu_busy_str) if gpu_busy_str else 0

        is_unified = gtt_total > vram_total * 4

        if is_unified:
            mem_total = gtt_total
            mem_used = gtt_used
        else:
            mem_total = vram_total
            mem_used = vram_used

        temp = 0
        power_w = None
        if hwmon:
            temp = _find_hwmon_temp(hwmon)
            power_w = _find_hwmon_power(hwmon)

        gpu_name = _read_sysfs(f"{base}/product_name")
        memory_type = "unified" if is_unified else "discrete"
        if not gpu_name:
            if is_unified:
                gpu_name = get_gpu_tier(mem_total / (1024**3), memory_type)
            else:
                gpu_name = "AMD Radeon"

        mem_used_mb = mem_used // (1024 * 1024)
        mem_total_mb = mem_total // (1024 * 1024)

        return GPUInfo(
            name=gpu_name,
            memory_used_mb=mem_used_mb,
            memory_total_mb=mem_total_mb,
            memory_percent=round(mem_used_mb / mem_total_mb * 100, 1) if mem_total_mb > 0 else 0,
            utilization_percent=gpu_busy,
            temperature_c=temp,
            power_w=power_w,
            memory_type=memory_type,
            gpu_backend="amd",
            utilization_available=bool(gpu_busy_str),
            temperature_available=temp > 0,
        )
    except (ValueError, TypeError):
        return None


def get_gpu_info_nvidia() -> Optional[GPUInfo]:
    """Get GPU metrics from nvidia-smi.

    Handles multi-GPU systems by summing VRAM across all GPUs and
    reporting aggregate utilization and peak temperature.
    """
    success, output = run_command([
        "nvidia-smi",
        "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits"
    ])

    if not success or not output:
        return None

    # nvidia-smi returns one line per GPU; split before parsing
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    if not lines:
        return None

    try:
        gpus = []
        any_unified = False
        for line in lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            power_w = None
            if len(parts) >= 6 and parts[5] not in ("[N/A]", "[Not Supported]", "N/A", "Not Supported", ""):
                try:
                    power_w = round(float(parts[5]), 1)
                except (ValueError, TypeError):
                    pass
            na_values = ("[N/A]", "[Not Supported]", "N/A", "Not Supported", "")
            # GB10/GB200 unified memory: nvidia-smi reports [N/A] for memory
            # fields; fall back to /proc/meminfo (mirrors detection.sh).
            unified = parts[1] in na_values or parts[2] in na_values
            if unified:
                fallback = _read_meminfo_mb()
                if not fallback:
                    continue
                mem_used, mem_total = fallback
                any_unified = True
            else:
                mem_used = int(parts[1])
                mem_total = int(parts[2])
            util = int(parts[3]) if parts[3] not in na_values else 0
            temp = int(parts[4]) if parts[4] not in na_values else 0
            gpus.append({
                "name": parts[0],
                "mem_used": mem_used,
                "mem_total": mem_total,
                "util": util,
                "temp": temp,
                "utilization_available": parts[3] not in na_values,
                "temperature_available": parts[4] not in na_values,
                "power_w": power_w,
            })

        if not gpus:
            return None

        memory_type = "unified" if any_unified else "discrete"

        if len(gpus) == 1:
            g = gpus[0]
            mem_used, mem_total = g["mem_used"], g["mem_total"]
            return GPUInfo(
                name=g["name"],
                memory_used_mb=mem_used,
                memory_total_mb=mem_total,
                memory_percent=round(mem_used / mem_total * 100, 1) if mem_total > 0 else 0,
                utilization_percent=g["util"],
                temperature_c=g["temp"],
                power_w=g["power_w"],
                memory_type=memory_type,
                gpu_backend="nvidia",
                utilization_available=g["utilization_available"],
                temperature_available=g["temperature_available"],
            )

        # Multi-GPU: aggregate across all GPUs
        mem_used = sum(g["mem_used"] for g in gpus)
        mem_total = sum(g["mem_total"] for g in gpus)
        avg_util = round(sum(g["util"] for g in gpus) / len(gpus))
        max_temp = max(g["temp"] for g in gpus)
        total_power: Optional[float] = None
        power_values = [g["power_w"] for g in gpus if g["power_w"] is not None]
        if power_values:
            total_power = round(sum(power_values), 1)

        # Build a display name: "RTX 4090 × 2" or "RTX 3090 + RTX 4090"
        names = [g["name"] for g in gpus]
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
            memory_percent=round(mem_used / mem_total * 100, 1) if mem_total > 0 else 0,
            utilization_percent=avg_util,
            temperature_c=max_temp,
            power_w=total_power,
            memory_type=memory_type,
            gpu_backend="nvidia",
            gpu_count=len(gpus),
            utilization_available=all(g["utilization_available"] for g in gpus),
            temperature_available=any(g["temperature_available"] for g in gpus),
        )
    except (ValueError, IndexError):
        pass

    return None


def get_gpu_info_apple() -> Optional[GPUInfo]:
    """Get GPU metrics for Apple Silicon via system_profiler (native) or env vars (container)."""
    gpu_backend = os.environ.get("GPU_BACKEND", "").lower()

    if platform.system() == "Darwin":
        try:
            # Get chip name
            success, chip_output = run_command(["sysctl", "-n", "machdep.cpu.brand_string"])
            chip_name = chip_output.strip() if success else "Apple Silicon"

            # Get total memory (unified memory on Apple Silicon)
            success, mem_output = run_command(["sysctl", "-n", "hw.memsize"])
            if not success:
                return None

            total_bytes = int(mem_output.strip())
            total_mb = total_bytes // (1024 * 1024)

            # Estimate used memory from vm_stat
            used_mb = 0
            success, vm_output = run_command(["vm_stat"])
            if success:
                import re
                pages = {}
                for line in vm_output.splitlines():
                    match = re.match(r"(.+?):\s+(\d+)", line)
                    if match:
                        pages[match.group(1).strip()] = int(match.group(2))
                page_size = 16384
                ps_match = re.search(r"page size of (\d+) bytes", vm_output)
                if ps_match:
                    page_size = int(ps_match.group(1))
                active = pages.get("Pages active", 0)
                wired = pages.get("Pages wired down", 0)
                compressed = pages.get("Pages occupied by compressor", 0)
                used_mb = (active + wired + compressed) * page_size // (1024 * 1024)

            return GPUInfo(
                name=chip_name,
                memory_used_mb=used_mb,
                memory_total_mb=total_mb,
                memory_percent=round(used_mb / total_mb * 100, 1) if total_mb > 0 else 0,
                utilization_percent=0,  # not easily available without IOKit
                temperature_c=0,
                power_w=None,
                memory_type="unified",
                gpu_backend="apple",
                memory_usage_available=False,
                utilization_available=False,
                temperature_available=False,
            )
        except (ValueError, TypeError) as e:
            logger.debug("Apple Silicon GPU detection failed: %s", e)
            return None

    elif gpu_backend == "apple":
        # Linux container path (Docker Desktop on macOS): use HOST_RAM_GB env var
        host_ram_gb_str = os.environ.get("HOST_RAM_GB", "")
        if not host_ram_gb_str:
            return None
        try:
            host_ram_gb_float = float(host_ram_gb_str)
        except ValueError:
            return None
        if host_ram_gb_float <= 0:
            return None
        total_mb = int(host_ram_gb_float * 1024)
        # Use /proc/meminfo for used memory (best available proxy inside container)
        # Note: used_mb reflects Docker Desktop VM memory pressure, not the host Mac's.
        # Total is correctly overridden by HOST_RAM_GB. See issue #102 for a future
        # host-metrics collector that would fix used_mb.
        used_mb = 0
        try:
            with open("/proc/meminfo") as f:
                meminfo = {}
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        meminfo[parts[0].rstrip(":")] = int(parts[1])
            avail = meminfo.get("MemAvailable", 0)
            total_kb = meminfo.get("MemTotal", 0)
            used_mb = (total_kb - avail) // 1024
        except OSError:
            pass
        return GPUInfo(
            name=f"Apple M-Series ({int(host_ram_gb_float)} GB Unified)",
            memory_used_mb=used_mb,
            memory_total_mb=total_mb,
            memory_percent=round(used_mb / total_mb * 100, 1) if total_mb > 0 else 0,
            utilization_percent=0,
            temperature_c=0,
            power_w=None,
            memory_type="unified",
            gpu_backend="apple",
            memory_usage_available=False,
            utilization_available=False,
            temperature_available=False,
        )

    return None


def _get_windows_host_gpu_payload() -> Optional[dict]:
    """Read and minimally validate the versioned Windows host GPU payload."""
    if os.environ.get("GPU_BACKEND", "").lower() != "amd":
        return None
    location = _read_env_var_from_file("AMD_INFERENCE_LOCATION") or os.environ.get(
        "AMD_INFERENCE_LOCATION", "",
    )
    if location.lower() != "host":
        return None
    try:
        payload = request_agent_json("GET", "/v1/gpu/metrics", timeout=10)
        if payload.get("schema_version") != "ods.host-gpu-metrics.v1":
            return None
        return payload
    except (AgentClientError, OSError, TypeError, ValueError):
        return None


def get_gpu_info_windows_host() -> Optional[GPUInfo]:
    """Read aggregate Windows AMD counters through the host agent."""
    payload = _get_windows_host_gpu_payload()
    if payload is None:
        return None
    try:
        total_mb = max(0, int(payload.get("memory_total_mb") or 0))
        used_mb = min(total_mb, max(0, int(payload.get("memory_used_mb") or 0)))
        utilization = max(0, min(100, int(payload.get("utilization_percent") or 0)))
        gpu_count = max(1, min(32, int(payload.get("gpu_count") or 1)))
    except (TypeError, ValueError):
        return None
    if not payload.get("name") or total_mb <= 0:
        return None
    return GPUInfo(
        name=str(payload["name"]),
        memory_used_mb=used_mb,
        memory_total_mb=total_mb,
        memory_percent=round(used_mb / total_mb * 100, 1),
        utilization_percent=utilization,
        temperature_c=int(payload.get("temperature_c") or 0),
        power_w=None,
        memory_type=(
            "unified" if payload.get("memory_type") == "unified" else "discrete"
        ),
        gpu_backend="amd",
        gpu_count=gpu_count,
        memory_usage_available=bool(payload.get("memory_usage_available", False)),
        utilization_available=bool(payload.get("utilization_available", False)),
        temperature_available=bool(payload.get("temperature_available", False)),
    )


def get_gpu_info_windows_host_detailed() -> Optional[list[IndividualGPU]]:
    """Read per-adapter Windows AMD counters when the host supports them."""
    payload = _get_windows_host_gpu_payload()
    if payload is None or not isinstance(payload.get("gpus"), list):
        return None
    result: list[IndividualGPU] = []
    try:
        for position, item in enumerate(payload["gpus"][:32]):
            if not isinstance(item, dict):
                continue
            total_mb = max(0, int(item.get("memory_total_mb") or 0))
            if not item.get("name") or total_mb <= 0:
                continue
            used_mb = min(total_mb, max(0, int(item.get("memory_used_mb") or 0)))
            utilization = max(0, min(100, int(item.get("utilization_percent") or 0)))
            result.append(IndividualGPU(
                index=max(0, min(31, int(item.get("index", position)))),
                uuid=str(item.get("uuid") or f"amd-windows-host-{position}"),
                name=str(item["name"]),
                memory_used_mb=used_mb,
                memory_total_mb=total_mb,
                memory_percent=round(used_mb / total_mb * 100, 1),
                utilization_percent=utilization,
                temperature_c=int(item.get("temperature_c") or 0),
                power_w=None,
                memory_type=(
                    "unified" if item.get("memory_type") == "unified" else "discrete"
                ),
                assigned_services=["llama-server"],
                memory_usage_available=bool(item.get("memory_usage_available", False)),
                utilization_available=bool(item.get("utilization_available", False)),
                temperature_available=bool(item.get("temperature_available", False)),
            ))
    except (TypeError, ValueError):
        return None
    return result or None


def get_gpu_info() -> Optional[GPUInfo]:
    """Get GPU metrics. Tries the configured backend first, then auto-detects."""
    gpu_backend = os.environ.get("GPU_BACKEND", "").lower()

    if gpu_backend == "amd":
        try:
            expected_count = int(os.environ.get("GPU_COUNT", "1"))
        except ValueError:
            expected_count = 1
        if expected_count > 1:
            detailed = get_gpu_info_amd_detailed()
            if detailed:
                return aggregate_gpu_details(detailed, "amd")
        detailed = get_gpu_info_windows_host_detailed()
        if detailed:
            return aggregate_gpu_details(detailed, "amd")
        info = get_gpu_info_amd()
        if info:
            return info
        info = get_gpu_info_windows_host()
        if info:
            return info

    if gpu_backend == "apple":
        info = get_gpu_info_apple()
        if info:
            return info

    info = get_gpu_info_nvidia()
    if info:
        return info

    if gpu_backend != "amd":
        info = get_gpu_info_amd()
        if info:
            return info

    # Auto-detect Apple Silicon if no backend specified and nothing else found
    if platform.system() == "Darwin":
        return get_gpu_info_apple()

    return None


# ============================================================================
# Topology — read from file written by installer / ods-cli
# ============================================================================

def read_gpu_topology() -> Optional[dict]:
    """Read GPU topology from config/gpu-topology.json if it exists.

    The file is written by the installer (03-features.sh) and refreshed by
    'ods gpu reassign'.  Inside the API container it is available at
    /ods/config/gpu-topology.json (mounted read-only).
    """
    install_dir = os.environ.get("ODS_INSTALL_DIR", os.path.expanduser("~/ods"))
    topo_path = Path(install_dir) / "config" / "gpu-topology.json"
    if not topo_path.exists():
        logger.warning("Topology file not found at %s", topo_path)
        return None
    try:
        return _json.loads(topo_path.read_text())
    except (OSError, _json.JSONDecodeError) as exc:
        logger.warning("Failed to read topology file %s: %s", topo_path, exc)
        return None


# ============================================================================
# Assignment decoding helpers
# ============================================================================

def decode_gpu_assignment() -> Optional[dict]:
    """Decode GPU_ASSIGNMENT_JSON_B64, preferring the live .env file over the
    container startup environment so reassignments are reflected without restart."""
    b64 = _read_env_var_from_file("GPU_ASSIGNMENT_JSON_B64") or os.environ.get("GPU_ASSIGNMENT_JSON_B64", "")
    if not b64:
        return None
    try:
        return _json.loads(base64.b64decode(b64.strip()).decode("utf-8"))
    except (base64.binascii.Error, _json.JSONDecodeError, UnicodeDecodeError):
        return None


def _read_env_var_from_file(key: str) -> str:
    """Read a single variable directly from the .env file (split on first '=' only)."""
    install_dir = os.environ.get("ODS_INSTALL_DIR", os.path.expanduser("~/ods"))
    env_path = Path(install_dir) / ".env"
    try:
        for line in env_path.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line[len(key) + 1:].strip().strip("\"'")
    except OSError:
        pass
    return ""


def _build_uuid_service_map(assignment: dict) -> dict[str, list[str]]:
    """Map GPU UUID → list of service names from assignment JSON."""
    result: dict[str, list[str]] = {}
    services = assignment.get("gpu_assignment", {}).get("services", {})
    for svc_name, svc_data in services.items():
        for uuid in svc_data.get("gpus", []):
            result.setdefault(uuid, []).append(svc_name)
    return result


def _infer_gpu_services_from_processes() -> dict[str, list[str]]:
    """Fallback: infer GPU service assignment from nvidia-smi compute processes.

    Used when GPU_ASSIGNMENT_JSON_B64 is not set (single-GPU setups).
    Maps GPU UUID -> list of likely service names based on running processes.
    """
    success, output = run_command([
        "nvidia-smi",
        "--query-compute-apps=gpu_uuid,pid,used_memory",
        "--format=csv,noheader,nounits",
    ])
    if not success or not output:
        return {}

    # Collect UUIDs that have active compute processes
    active_uuids: dict[str, int] = {}  # uuid -> total used memory MB
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            uuid = parts[0]
            try:
                mem = int(parts[2])
            except ValueError:
                mem = 0
            active_uuids[uuid] = active_uuids.get(uuid, 0) + mem

    if not active_uuids:
        return {}

    # For each active GPU, attribute to llama-server (the primary GPU consumer)
    result: dict[str, list[str]] = {}
    for uuid, mem_mb in active_uuids.items():
        if mem_mb > 100:
            result[uuid] = ["llama-server"]

    return result


# ============================================================================
# Per-GPU detailed detection
# ============================================================================

def get_gpu_info_nvidia_detailed() -> Optional[list[IndividualGPU]]:
    """Return one IndividualGPU per NVIDIA GPU, with assigned_services populated.

    Returns None if nvidia-smi is unavailable or returns no data.
    """
    success, output = run_command([
        "nvidia-smi",
        "--query-gpu=index,uuid,name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ])
    if not success or not output:
        return None

    lines = [ln.strip() for ln in output.strip().splitlines() if ln.strip()]
    if not lines:
        return None

    assignment = decode_gpu_assignment()
    if assignment:
        uuid_service_map = _build_uuid_service_map(assignment)
    else:
        uuid_service_map = _infer_gpu_services_from_processes()

    gpus: list[IndividualGPU] = []
    na_values = ("[N/A]", "[Not Supported]", "N/A", "Not Supported", "")
    for line in lines:
        try:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 7:
                continue
            power_w = None
            if len(parts) >= 8 and parts[7] not in na_values:
                try:
                    power_w = round(float(parts[7]), 1)
                except (ValueError, TypeError):
                    pass
            # GB10/GB200 unified memory fallback (see _read_meminfo_mb).
            if parts[3] in na_values or parts[4] in na_values:
                fallback = _read_meminfo_mb()
                if not fallback:
                    continue
                mem_used, mem_total = fallback
            else:
                mem_used = int(parts[3])
                mem_total = int(parts[4])
            uuid = parts[1]
            gpus.append(IndividualGPU(
                index=int(parts[0]),
                uuid=uuid,
                name=parts[2],
                memory_used_mb=mem_used,
                memory_total_mb=mem_total,
                memory_percent=round(mem_used / mem_total * 100, 1) if mem_total > 0 else 0.0,
                utilization_percent=int(parts[5]) if parts[5] not in na_values else 0,
                temperature_c=int(parts[6]) if parts[6] not in na_values else 0,
                power_w=power_w,
                assigned_services=uuid_service_map.get(uuid, []),
                utilization_available=parts[5] not in na_values,
                temperature_available=parts[6] not in na_values,
            ))
        except (ValueError, IndexError):
            logger.warning("Skipping unparseable nvidia-smi row: %s", line)

    return gpus or None


def _find_hwmon_temp(hwmon_dir: str) -> int:
    """Find GPU temperature via hwmon label-based discovery (junction > edge > temp1)."""
    import glob as _glob
    # Map each label to its temp*_input first, so the junction-over-edge
    # preference holds regardless of which tempN node carries each label.
    # On many AMD cards edge is temp1 and junction is temp2; scanning in file
    # order would otherwise return edge even when the more accurate junction
    # die temperature is available.
    inputs_by_label: dict[str, str] = {}
    for label_path in sorted(_glob.glob(f"{hwmon_dir}/temp*_label")):
        label = _read_sysfs(label_path)
        if label:
            inputs_by_label.setdefault(label.lower(), label_path.replace("_label", "_input"))
    for preferred in ("junction", "edge"):
        input_path = inputs_by_label.get(preferred)
        if input_path:
            temp_str = _read_sysfs(input_path)
            if temp_str:
                return int(temp_str) // 1000
    # Fallback to temp1_input
    temp_str = _read_sysfs(f"{hwmon_dir}/temp1_input")
    return int(temp_str) // 1000 if temp_str else 0


def _find_hwmon_power(hwmon_dir: str) -> Optional[float]:
    """Read GPU power: power1_average → power1_input fallback (microwatts → watts)."""
    for attr in ("power1_average", "power1_input"):
        power_str = _read_sysfs(f"{hwmon_dir}/{attr}")
        if power_str:
            return round(int(power_str) / 1e6, 1)
    return None


def get_gpu_info_amd_detailed() -> Optional[list[IndividualGPU]]:
    """Return one IndividualGPU per AMD GPU by iterating all amdgpu sysfs cards.

    Uses topology JSON (if available) for stable UUIDs and assignment mapping.
    Falls back to card index as UUID.
    Returns None if no AMD GPUs are found.
    """
    import glob as _glob
    card_dirs = sorted(_glob.glob("/sys/class/drm/card*/device"))
    amd_cards = [d for d in card_dirs if _read_sysfs(f"{d}/vendor") == "0x1002"]
    if not amd_cards:
        return None

    # Load topology for UUID mapping and assignment for service mapping
    topo = read_gpu_topology()
    topo_gpus = topo.get("gpus", []) if topo else []
    # Build index→topology entry lookup
    topo_by_index = {g["index"]: g for g in topo_gpus if "index" in g}

    assignment = decode_gpu_assignment()
    uuid_service_map = _build_uuid_service_map(assignment) if assignment else {}

    gpus: list[IndividualGPU] = []
    for idx, base in enumerate(amd_cards):
        hwmon = _find_hwmon_dir(base)
        try:
            vram_total_str = _read_sysfs(f"{base}/mem_info_vram_total")
            vram_used_str = _read_sysfs(f"{base}/mem_info_vram_used")
            gtt_total_str = _read_sysfs(f"{base}/mem_info_gtt_total")
            gtt_used_str = _read_sysfs(f"{base}/mem_info_gtt_used")
            gpu_busy_str = _read_sysfs(f"{base}/gpu_busy_percent")

            if not vram_total_str or not vram_used_str:
                continue

            vram_total = int(vram_total_str)
            vram_used = int(vram_used_str)
            gtt_total = int(gtt_total_str) if gtt_total_str else 0
            gtt_used = int(gtt_used_str) if gtt_used_str else 0
            gpu_busy = int(gpu_busy_str) if gpu_busy_str else 0

            is_unified = gtt_total > vram_total * 4
            mem_total = gtt_total if is_unified else vram_total
            mem_used = gtt_used if is_unified else vram_used

            temp = 0
            power_w = None
            if hwmon:
                temp = _find_hwmon_temp(hwmon)
                power_w = _find_hwmon_power(hwmon)

            # Use topology UUID if available, otherwise fall back to card index
            topo_entry = topo_by_index.get(idx, {})
            gpu_uuid = topo_entry.get("uuid", f"card{idx}")
            gpu_name = topo_entry.get("name") or _read_sysfs(f"{base}/product_name") or "AMD Radeon"
            mem_used_mb = mem_used // (1024 * 1024)
            mem_total_mb = mem_total // (1024 * 1024)

            gpus.append(IndividualGPU(
                index=idx,
                uuid=gpu_uuid,
                name=gpu_name,
                memory_used_mb=mem_used_mb,
                memory_total_mb=mem_total_mb,
                memory_percent=round(mem_used_mb / mem_total_mb * 100, 1) if mem_total_mb > 0 else 0.0,
                utilization_percent=gpu_busy,
                temperature_c=temp,
                power_w=power_w,
                memory_type="unified" if is_unified else "discrete",
                assigned_services=uuid_service_map.get(gpu_uuid, []),
                utilization_available=bool(gpu_busy_str),
                temperature_available=temp > 0,
            ))
        except (ValueError, TypeError):
            continue

    return gpus or None


def aggregate_gpu_details(gpus: list[IndividualGPU], backend: str) -> GPUInfo:
    """Build one truthful aggregate shared by status and detailed GPU routes."""
    if not gpus:
        raise ValueError("at least one GPU is required")

    mem_used = sum(gpu.memory_used_mb for gpu in gpus)
    mem_total = sum(gpu.memory_total_mb for gpu in gpus)
    available_utilization = [
        gpu.utilization_percent for gpu in gpus if gpu.utilization_available
    ]
    available_temperatures = [
        gpu.temperature_c for gpu in gpus if gpu.temperature_available
    ]
    available_power = [gpu.power_w for gpu in gpus if gpu.power_w is not None]
    names = [gpu.name for gpu in gpus]
    if len(set(names)) == 1:
        display_name = names[0] if len(names) == 1 else f"{names[0]} \u00d7 {len(names)}"
    else:
        display_name = " + ".join(names[:2])
        if len(names) > 2:
            display_name += f" + {len(names) - 2} more"

    return GPUInfo(
        name=display_name,
        memory_used_mb=mem_used,
        memory_total_mb=mem_total,
        memory_percent=round(mem_used / mem_total * 100, 1) if mem_total > 0 else 0.0,
        utilization_percent=(
            round(sum(available_utilization) / len(available_utilization))
            if available_utilization else 0
        ),
        temperature_c=max(available_temperatures) if available_temperatures else 0,
        power_w=round(sum(available_power), 1) if available_power else None,
        memory_type=(
            "unified" if all(gpu.memory_type == "unified" for gpu in gpus)
            else "discrete"
        ),
        gpu_backend=backend,
        gpu_count=len(gpus),
        memory_usage_available=all(gpu.memory_usage_available for gpu in gpus),
        utilization_available=bool(available_utilization),
        temperature_available=bool(available_temperatures),
    )


def get_gpu_tier(vram_gb: float, memory_type: str = "discrete") -> str:
    """Get tier name based on VRAM."""
    if memory_type == "unified":
        if vram_gb >= 90:
            return "Strix Halo 90+"
        else:
            return "Strix Halo Compact"
    if vram_gb >= 80:
        return "Professional"
    elif vram_gb >= 24:
        return "Prosumer"
    elif vram_gb >= 16:
        return "Standard"
    elif vram_gb >= 8:
        return "Entry"
    else:
        return "Minimal"
