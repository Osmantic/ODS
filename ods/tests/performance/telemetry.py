#!/usr/bin/env python3
"""Read-only resource sampler for ODS performance experiments.

Standalone CLI. Emits JSONL samples. Never mutates system state, never
signals processes, never shells out with shell=True, never reads
credentials/environ/cmdlines/user data.
"""

import argparse
import ctypes
import datetime as _dt
import json
import math
import os
import platform
import re
import subprocess
import sys
import time

SCHEMA_VERSION = 1
SUBPROCESS_TIMEOUT = 2.0
ERROR_CODES = frozenset({
    "linux_meminfo_unavailable",
    "linux_meminfo_parse_error",
    "linux_vmstat_unavailable",
    "linux_vmstat_parse_error",
    "mac_sysctl_memsize_unavailable",
    "mac_sysctl_memsize_parse_error",
    "mac_vm_stat_unavailable",
    "mac_vm_stat_parse_error",
    "mac_swapusage_unavailable",
    "mac_swapusage_parse_error",
    "mac_pressure_unavailable",
    "mac_pressure_parse_error",
    "win_global_memory_status_error",
    "nvidia_smi_unavailable",
    "nvidia_smi_timeout",
    "nvidia_smi_parse_error",
    "nvidia_smi_nonzero_exit",
    "sample_overrun",
    "unsupported_platform",
})


def _utc_now_iso():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")


def _run(cmd, timeout=SUBPROCESS_TIMEOUT):
    """Run a fixed argv list, no shell. Returns (rc, stdout, stderr, timed_out)."""
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            shell=False,
        )
        return p.returncode, p.stdout, p.stderr, False
    except subprocess.TimeoutExpired:
        return None, b"", b"", True
    except (FileNotFoundError, PermissionError, OSError):
        return None, b"", b"", False


# ---------- Pure parsers ----------

_MEMINFO_UNITS = {
    "B": 1,
    "kB": 1024,
    "KB": 1024,
    "MB": 1024 * 1024,
    "GB": 1024 * 1024 * 1024,
}


def parse_meminfo(text):
    """Parse /proc/meminfo. Returns dict of byte values for known keys."""
    out = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        parts = rest.strip().split()
        if not parts:
            continue
        try:
            val = int(parts[0])
        except ValueError:
            continue
        unit = parts[1] if len(parts) > 1 else "B"
        mult = _MEMINFO_UNITS.get(unit)
        if mult is None:
            continue
        if val >= 0:
            out[key.strip()] = val * mult
    return out


def parse_vmstat(text):
    """Parse /proc/vmstat into {key: int}. Values are cumulative counters."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            out[parts[0]] = int(parts[1])
        except ValueError:
            continue
    return out


def parse_vm_stat(text):
    """Parse macOS vm_stat output.

    Returns dict with 'page_size' (bytes) and page counts keyed by
    normalized names. Does NOT sum free+inactive+purgeable.
    """
    page_size = None
    counts = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.search(r"page size of (\d+) bytes", line)
        if m:
            page_size = int(m.group(1))
            continue
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        rest = rest.strip().rstrip(".")
        try:
            counts[key.strip()] = int(rest)
        except ValueError:
            continue
    return {"page_size": page_size, "counts": counts}


def parse_swapusage(text):
    """Parse `sysctl -n vm.swapusage` output.

    Example: 'total = 2048.00M  used = 512.50M  free = 1535.50M  (encrypted)'
    Returns dict with total_bytes, used_bytes, free_bytes (any may be None).
    """
    units = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}
    out = {"total_bytes": None, "used_bytes": None, "free_bytes": None}
    for field in ("total", "used", "free"):
        m = re.search(rf"\b{field}\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT])", text)
        if not m:
            continue
        try:
            val = float(m.group(1))
        except ValueError:
            continue
        mult = units.get(m.group(2))
        if mult is None:
            continue
        out[f"{field}_bytes"] = int(val * mult)
    return out


def parse_nvidia_smi_csv(text):
    """Parse nvidia-smi --query-gpu=... --format=csv,noheader,nounits.

    Returns list of per-GPU dicts. Numeric fields parsed; '[N/A]' or
    non-numeric become None. Preserves GPU order (index).
    """
    gpus = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 7:
            continue
        idx, mem_total, mem_used, mem_free, util, temp, power = parts
        def _num(s):
            try:
                value = float(s)
                return value if math.isfinite(value) and value >= 0 else None
            except ValueError:
                return None
        gpus.append({
            "index": int(idx) if idx.isdigit() else None,
            "memory_total_mib": _num(mem_total),
            "memory_used_mib": _num(mem_used),
            "memory_free_mib": _num(mem_free),
            "utilization_gpu_pct": _num(util),
            "temperature_gpu_c": _num(temp),
            "power_draw_w": _num(power),
        })
    return gpus


# ---------- Platform collectors ----------


def _collect_linux(errors):
    metrics = {
        "ram_total_bytes": None,
        "ram_available_bytes": None,
        "ram_available_semantics": "memavailable",
        "swap_total_bytes": None,
        "swap_free_bytes": None,
        "swap_in_pages_cumulative": None,
        "swap_out_pages_cumulative": None,
        "page_size_bytes": None,
    }
    try:
        with open("/proc/meminfo", "r", encoding="ascii", errors="replace") as f:
            mi = parse_meminfo(f.read())
        metrics["ram_total_bytes"] = mi.get("MemTotal")
        metrics["ram_available_bytes"] = mi.get("MemAvailable")
        metrics["swap_total_bytes"] = mi.get("SwapTotal")
        metrics["swap_free_bytes"] = mi.get("SwapFree")
        if any(mi.get(k) is None for k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")):
            errors.append("linux_meminfo_parse_error")
    except OSError:
        errors.append("linux_meminfo_unavailable")
    try:
        with open("/proc/vmstat", "r", encoding="ascii", errors="replace") as f:
            vs = parse_vmstat(f.read())
        metrics["swap_in_pages_cumulative"] = vs.get("pswpin")
        metrics["swap_out_pages_cumulative"] = vs.get("pswpout")
        if vs.get("pswpin") is None or vs.get("pswpout") is None:
            errors.append("linux_vmstat_parse_error")
    except OSError:
        errors.append("linux_vmstat_unavailable")
    try:
        metrics["page_size_bytes"] = os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        pass
    return metrics


def _collect_macos(errors):
    metrics = {
        "ram_total_bytes": None,
        "ram_available_bytes": None,
        "ram_available_semantics": "unsupported_macos",
        "swap_total_bytes": None,
        "swap_used_bytes": None,
        "swap_free_bytes": None,
        "page_size_bytes": None,
        "vm_free_pages": None,
        "vm_active_pages": None,
        "vm_inactive_pages": None,
        "vm_speculative_pages": None,
        "vm_wired_pages": None,
        "vm_purgeable_pages": None,
        "vm_compressor_pages": None,
        "swapins_cumulative": None,
        "swapouts_cumulative": None,
        "memory_pressure_dispatch_level": None,
    }
    rc, out, _err, to = _run(["sysctl", "-n", "hw.memsize"])
    if to or rc != 0:
        errors.append("mac_sysctl_memsize_unavailable")
    else:
        try:
            metrics["ram_total_bytes"] = int(out.decode("ascii", "replace").strip())
        except ValueError:
            errors.append("mac_sysctl_memsize_parse_error")
    rc, out, _err, to = _run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"])
    if to or rc != 0:
        errors.append("mac_pressure_unavailable")
    else:
        try:
            level = int(out.decode("ascii", "replace").strip())
            if level not in (1, 2, 4):
                raise ValueError("unknown dispatch level")
            metrics["memory_pressure_dispatch_level"] = level
        except ValueError:
            errors.append("mac_pressure_parse_error")
    rc, out, _err, to = _run(["vm_stat"])
    if to or rc != 0:
        errors.append("mac_vm_stat_unavailable")
    else:
        parsed = parse_vm_stat(out.decode("ascii", "replace"))
        ps = parsed["page_size"]
        metrics["page_size_bytes"] = ps
        c = parsed["counts"]
        if not ps or any(c.get(k) is None for k in ("Pages free", "Swapins", "Swapouts")):
            errors.append("mac_vm_stat_parse_error")
        metrics["vm_free_pages"] = c.get("Pages free")
        metrics["vm_active_pages"] = c.get("Pages active")
        metrics["vm_inactive_pages"] = c.get("Pages inactive")
        metrics["vm_speculative_pages"] = c.get("Pages speculative")
        metrics["vm_wired_pages"] = c.get("Pages wired down")
        metrics["vm_purgeable_pages"] = c.get("Pages purgeable")
        metrics["vm_compressor_pages"] = c.get("Pages occupied by compressor")
        metrics["swapins_cumulative"] = c.get("Swapins")
        metrics["swapouts_cumulative"] = c.get("Swapouts")
    rc, out, _err, to = _run(["sysctl", "-n", "vm.swapusage"])
    if to or rc != 0:
        errors.append("mac_swapusage_unavailable")
    else:
        su = parse_swapusage(out.decode("ascii", "replace"))
        metrics["swap_total_bytes"] = su["total_bytes"]
        metrics["swap_used_bytes"] = su["used_bytes"]
        metrics["swap_free_bytes"] = su["free_bytes"]
        if any(v is None for v in su.values()):
            errors.append("mac_swapusage_parse_error")
    return metrics


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _collect_windows(errors):
    metrics = {
        "ram_total_bytes": None,
        "ram_available_bytes": None,
        "ram_available_semantics": "global_memory_status_ex",
        "commit_limit_bytes": None,
        "commit_available_bytes": None,
    }
    try:
        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        if not ok:
            errors.append("win_global_memory_status_error")
            return metrics
        metrics["ram_total_bytes"] = int(stat.ullTotalPhys)
        metrics["ram_available_bytes"] = int(stat.ullAvailPhys)
        metrics["commit_limit_bytes"] = int(stat.ullTotalPageFile)
        metrics["commit_available_bytes"] = int(stat.ullAvailPageFile)
    except (AttributeError, OSError):
        errors.append("win_global_memory_status_error")
    return metrics


def _collect_nvidia(errors):
    metrics = {"gpus": None}
    cmd = [
        "nvidia-smi",
        "--query-gpu=index,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ]
    rc, out, _err, to = _run(cmd)
    if to:
        errors.append("nvidia_smi_timeout")
        return metrics
    if rc is None:
        errors.append("nvidia_smi_unavailable")
        return metrics
    if rc != 0:
        errors.append("nvidia_smi_nonzero_exit")
        return metrics
    gpus = parse_nvidia_smi_csv(out.decode("ascii", "replace"))
    if not gpus:
        errors.append("nvidia_smi_parse_error")
        return metrics
    metrics["gpus"] = gpus
    return metrics


def collect_sample(scope, nvidia):
    """Collect one sample. Returns (metrics, errors)."""
    errors = []
    sysname = platform.system()
    if sysname == "Linux":
        metrics = _collect_linux(errors)
    elif sysname == "Darwin":
        metrics = _collect_macos(errors)
    elif sysname == "Windows":
        metrics = _collect_windows(errors)
    else:
        metrics = {}
        errors.append("unsupported_platform")
    if nvidia:
        metrics.update(_collect_nvidia(errors))
    return metrics, errors


# ---------- CLI ----------


def _build_parser():
    p = argparse.ArgumentParser(
        prog="telemetry",
        description="Read-only resource sampler for ODS performance experiments.",
    )
    p.add_argument("--output", required=True, help="JSONL output path (exclusive create).")
    p.add_argument("--duration", type=int, required=True, help="Total duration seconds (1..1800).")
    p.add_argument("--interval", type=int, required=True, help="Sample interval seconds (1..10).")
    p.add_argument("--scope", required=True, choices=["host", "wsl", "container"], help="Sampling scope.")
    p.add_argument("--nvidia", action="store_true", help="Include NVIDIA GPU metrics via nvidia-smi.")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    if not (1 <= args.duration <= 1800):
        print("error: --duration must be 1..1800", file=sys.stderr)
        return 2
    if not (1 <= args.interval <= 10):
        print("error: --interval must be 1..10", file=sys.stderr)
        return 2
    try:
        fh = open(args.output, "x", encoding="utf-8")
    except FileExistsError:
        print(f"error: refusing to overwrite existing file: {args.output}", file=sys.stderr)
        return 3

    sysname = platform.system()
    seq = 0
    start = time.perf_counter()
    deadline = start + args.duration
    next_due = start
    try:
        while True:
            now = time.perf_counter()
            if now >= deadline:
                break
            if now < next_due:
                time.sleep(min(next_due - now, deadline - now))
                now = time.perf_counter()
                if now >= deadline:
                    break
            t0 = time.perf_counter_ns()
            c0 = time.process_time_ns()
            metrics, errors = collect_sample(args.scope, args.nvidia)
            t1 = time.perf_counter_ns()
            c1 = time.process_time_ns()
            sample_duration_ms = (t1 - t0) / 1e6
            lag_ms = max(0.0, (now - next_due) * 1000.0)
            if lag_ms > args.interval * 1000 or sample_duration_ms > args.interval * 1000:
                errors.append("sample_overrun")
            metrics["sampler_overhead"] = {
                "perf_counter_delta_ns": t1 - t0,
                "process_time_delta_ns": c1 - c0,
                "lag_ms": lag_ms,
            }
            seq += 1
            record = {
                "schema_version": SCHEMA_VERSION,
                "sequence": seq,
                "utc_iso": _utc_now_iso(),
                "perf_counter_ns": t1,
                "platform": sysname,
                "scope": args.scope,
                "sample_duration_ms": sample_duration_ms,
                "errors": [e for e in errors if e in ERROR_CODES],
                "metrics": metrics,
            }
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
            fh.flush()
            next_due += args.interval
            after_sample = time.perf_counter()
            if next_due <= after_sample:
                # Do not issue catch-up bursts after an overlong sample.
                next_due = after_sample + args.interval
    except KeyboardInterrupt:
        outcome = "interrupted"
    else:
        outcome = "complete"
    finally:
        try:
            # Terminal marker does not perform another potentially slow sample.
            fh.write(json.dumps({"schema_version": SCHEMA_VERSION,
                "sequence": seq + 1, "terminal": True,
                "outcome": locals().get("outcome", "error"),
                "utc_iso": _utc_now_iso(), "perf_counter_ns": time.perf_counter_ns(),
                "platform": sysname, "scope": args.scope}) + "\n")
            fh.flush()
        finally:
            fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
