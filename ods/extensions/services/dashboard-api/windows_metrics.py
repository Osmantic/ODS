"""Windows CPU and RAM readings through kernel32, without a child process.

The arithmetic is pure so it is tested on every platform; only the two
readers touch ctypes, and they exist only on Windows.
"""

from __future__ import annotations

import sys
from typing import Optional

# (idle, kernel, user) in 100 ns units from GetSystemTimes. Kernel time
# includes idle time, so busy = kernel + user - idle.
SystemTimes = tuple[int, int, int]

_previous_times: Optional[SystemTimes] = None


def cpu_percent(previous: Optional[SystemTimes], current: SystemTimes) -> Optional[float]:
    """Busy share between two samples, or None before there are two."""
    if previous is None:
        return None
    idle = current[0] - previous[0]
    total = (current[1] - previous[1]) + (current[2] - previous[2])
    if total <= 0:
        return None
    return max(0.0, min(100.0, round((total - idle) / total * 100, 1)))


def ram_usage(total_bytes: int, available_bytes: int) -> dict:
    """The same shape as the Linux /proc/meminfo reading."""
    if total_bytes <= 0:
        return {"used_gb": None, "total_gb": None, "percent": None}
    used = max(0, total_bytes - available_bytes)
    return {
        "used_gb": round(used / 1024 ** 3, 1),
        "total_gb": round(total_bytes / 1024 ** 3, 1),
        "percent": max(0.0, min(100.0, round(used / total_bytes * 100, 1))),
    }


# kernel32 exists only on Windows. A module-level platform branch is what
# type checkers running on Linux understand; the other branch raises.
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    def _read_system_times() -> SystemTimes:
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            raise ctypes.WinError()

        def ticks(value: wintypes.FILETIME) -> int:
            return (value.dwHighDateTime << 32) | value.dwLowDateTime

        return ticks(idle), ticks(kernel), ticks(user)

    def _read_memory_status() -> tuple[int, int]:
        class MEMORYSTATUSEX(ctypes.Structure):
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

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise ctypes.WinError()
        return status.ullTotalPhys, status.ullAvailPhys

else:
    def _read_system_times() -> SystemTimes:
        raise OSError("Windows CPU times are only available on Windows")

    def _read_memory_status() -> tuple[int, int]:
        raise OSError("Windows memory status is only available on Windows")


def get_cpu_metrics() -> dict:
    global _previous_times
    current = _read_system_times()
    percent = cpu_percent(_previous_times, current)
    _previous_times = current
    return {"percent": percent, "temp_c": None}


def get_ram_metrics() -> dict:
    return ram_usage(*_read_memory_status())
