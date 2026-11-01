"""Host CPU and RAM metrics for dashboard-api running natively on Windows.

Reads kernel32 directly through ctypes (GetSystemTimes, GlobalMemoryStatusEx):
no extra dependency, no subprocess, no WMI. GPU telemetry stays with the host
agent. Callers pass *kernel32* in tests; on Windows it defaults to
``ctypes.windll.kernel32``.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

_GIB = 1024 ** 3


class _FileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _kernel32():
    return ctypes.windll.kernel32  # type: ignore[attr-defined]  # Windows only


def _ticks(value: _FileTime) -> int:
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


# Previous (idle, kernel, user) sample: CPU load is a delta between two calls.
_previous_times: tuple[int, int, int] | None = None
_lock = threading.Lock()


def _reset_cpu_sample() -> None:
    global _previous_times
    with _lock:
        _previous_times = None


def cpu_metrics(kernel32=None) -> dict:
    """Host CPU load since the previous call; ``percent`` is None on the first.

    GetSystemTimes reports kernel time including idle time, so busy time is
    (kernel + user) - idle over the interval.
    """
    global _previous_times
    k32 = kernel32 if kernel32 is not None else _kernel32()
    idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
    if not k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        raise OSError("GetSystemTimes failed")
    current = (_ticks(idle), _ticks(kernel), _ticks(user))
    percent = None
    with _lock:
        previous, _previous_times = _previous_times, current
    if previous is not None:
        d_idle, d_kernel, d_user = (now - before for now, before in zip(current, previous))
        total = d_kernel + d_user
        if total > 0 and d_idle >= 0:
            percent = max(0.0, min(100.0, round((total - d_idle) / total * 100, 1)))
    return {"percent": percent, "temp_c": None, "scope": "host", "source": "windows-kernel32"}


def ram_metrics(kernel32=None) -> dict:
    """Physical memory in use (total minus available), as GiB and percent."""
    k32 = kernel32 if kernel32 is not None else _kernel32()
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    if not k32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed")
    total, available = status.ullTotalPhys, status.ullAvailPhys
    if total <= 0 or available > total:
        raise ValueError("GlobalMemoryStatusEx reported inconsistent memory totals")
    used = total - available
    return {
        "used_gb": round(used / _GIB, 1),
        "total_gb": round(total / _GIB, 1),
        "percent": round(used / total * 100, 1),
        "scope": "host",
        "source": "windows-kernel32",
    }
