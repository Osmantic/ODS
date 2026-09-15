#!/usr/bin/env python3
"""Verify systemd timer OnCalendar generation in memory-shepherd installer."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "memory-shepherd"


class MemoryShepherdTimerStaggerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ods-timer-stagger-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "memory-shepherd.conf"
        self.units = self.root / "units"

    def test_stagger_minutes_wrap_into_hour_offsets(self):
        # Configure 8 agents so stagger exceeds 50 minutes (agent 6 would be 60m, agent 7 70m)
        lines = ["[general]", f"baseline_dir={self.root}", f"archive_dir={self.root}/archives"]
        for i in range(8):
            lines.extend([f"[agent{i}]", f"memory_file={self.root}/agent{i}.md", "baseline=base.md"])
        self.config.write_text("\n".join(lines) + "\n")

        env = dict(os.environ, MEMORY_SHEPHERD_CONF=str(self.config))
        result = subprocess.run(
            ["bash", str(SCRIPTS / "install.sh"), "--dry-run", "--prefix", str(self.units)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        # Parse all OnCalendar lines from the installer output
        calendar_specs = re.findall(r"OnCalendar=(.*)", result.stdout)
        self.assertTrue(len(calendar_specs) >= 8, f"Expected at least 8 calendar specs, got: {calendar_specs}")

        # Ensure all minutes in OnCalendar are strictly within [00, 59]
        for spec in calendar_specs:
            match = re.search(r"(\d{2})/3:(\d{2}):00", spec)
            if match:
                hour_offset = int(match.group(1))
                minute = int(match.group(2))
                self.assertLess(hour_offset, 3, f"Hour offset should be 0, 1, or 2 in 3-hour window: {spec}")
                self.assertLess(minute, 60, f"Minute must be strictly less than 60 in systemd spec: {spec}")

        # Verify specific expected offsets
        self.assertIn("*-*-* 00/3:00:00", result.stdout)
        self.assertIn("*-*-* 00/3:50:00", result.stdout)
        self.assertIn("*-*-* 01/3:00:00", result.stdout)
        self.assertIn("*-*-* 01/3:10:00", result.stdout)
        self.assertNotIn("00/3:60:00", result.stdout)
        self.assertNotIn("00/3:70:00", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
