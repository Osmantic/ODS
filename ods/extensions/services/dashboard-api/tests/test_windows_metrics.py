"""Windows CPU and RAM readings come from kernel32, not a child process."""

import sys

import pytest

import windows_metrics
from windows_metrics import cpu_percent, ram_usage


def test_first_cpu_sample_has_no_percent():
    assert cpu_percent(None, (10, 20, 30)) is None


def test_cpu_percent_counts_kernel_idle_once():
    # Kernel time includes idle: 100 ticks elapsed (kernel 60 + user 40),
    # 25 of them idle, so the CPU was busy 75% of the interval.
    assert cpu_percent((0, 0, 0), (25, 60, 40)) == 75.0


def test_cpu_percent_without_elapsed_time_is_unknown():
    assert cpu_percent((5, 10, 10), (5, 10, 10)) is None


def test_cpu_percent_is_clamped():
    assert cpu_percent((0, 0, 0), (0, 50, 50)) == 100.0
    assert cpu_percent((0, 0, 0), (200, 60, 40)) == 0.0


def test_ram_usage_matches_the_linux_shape():
    gib = 1024 ** 3
    assert ram_usage(16 * gib, 4 * gib) == {"used_gb": 12.0, "total_gb": 16.0, "percent": 75.0}


def test_ram_usage_without_a_total_is_unknown():
    assert ram_usage(0, 0) == {"used_gb": None, "total_gb": None, "percent": None}


@pytest.mark.skipif(sys.platform != "win32", reason="reads kernel32")
def test_real_readings_on_windows():
    windows_metrics.get_cpu_metrics()
    cpu = windows_metrics.get_cpu_metrics()
    ram = windows_metrics.get_ram_metrics()
    assert cpu["percent"] is None or 0.0 <= cpu["percent"] <= 100.0
    assert ram["total_gb"] > 0 and 0.0 < ram["percent"] <= 100.0


@pytest.mark.skipif(sys.platform != "win32", reason="reads kernel32")
def test_helpers_report_kernel32_on_windows():
    import helpers

    assert helpers.get_cpu_metrics()["source"] == "windows-kernel32"
    ram = helpers.get_ram_metrics()
    assert ram["source"] == "windows-kernel32" and ram["total_gb"] > 0
