"""Check installer-generated per-agent schedules with systemd's calendar parser."""
from datetime import datetime, timedelta
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="Requires systemd calendar parser")
def test_every_agent_gets_a_valid_three_hour_schedule(tmp_path):
    source = Path(__file__).resolve().parents[1] / "memory-shepherd/install.sh"
    script = tmp_path / "install.sh"
    shutil.copy2(source, script)
    config = tmp_path / "memory-shepherd.conf"
    config.write_text("".join(f"[agent-{index:02d}]\n" for index in range(20)))
    result = subprocess.run(["bash", str(script), "--dry-run", "--prefix", str(tmp_path / "units")],
                            capture_output=True, text=True, timeout=10,
                            env={**os.environ, "MEMORY_SHEPHERD_CONF": str(config)})
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "units").exists()
    # Include the all-agents timer and agents across the first hour and the
    # complete three-hour staggering window, including wraparound.
    schedules = re.findall(r"^OnCalendar=(.+)$", result.stdout, re.MULTILINE)
    assert len(schedules) == 21
    base = datetime(2026, 1, 1)
    for index, calendar in enumerate(schedules):
        checked = subprocess.run([
            "systemd-analyze", "calendar", "--base-time=2026-01-01 00:00:00 UTC",
            "--iterations=2", calendar], capture_output=True, text=True, timeout=10,
            env={**os.environ, "TZ": "UTC", "LC_ALL": "C", "SYSTEMD_COLORS": "0"})
        assert checked.returncode == 0, f"timer {index}: {checked.stderr}"
        stamps = re.findall(r"(?:Next elapse|Iteration #2): \w+ (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC", checked.stdout)
        assert len(stamps) == 2, checked.stdout
        first, second = (datetime.fromisoformat(stamp) for stamp in stamps)
        offset = 0 if index == 0 else ((index - 1) * 10) % 180
        assert first == base + timedelta(minutes=offset or 180)
        assert second - first == timedelta(hours=3)
