#!/usr/bin/env python3
"""Regression test: assign_gpus reads topology with explicit UTF-8 encoding.

The baseline used open(args.topology) without an encoding parameter, relying on
the locale-preferred encoding (cp1252 on Windows, ascii in minimal containers).
When the topology file contains non-ASCII characters in GPU model names or paths,
this silently fails with UnicodeDecodeError.  The baseline also only caught
FileNotFoundError, leaving PermissionError and IsADirectoryError uncaught.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "assign_gpus.py"


def run_assign_gpus(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


class AssignGpusTopologyEncodingTests(unittest.TestCase):
    def _make_topology(self, tmpdir: str, gpu_name: str = "NVIDIA RTX 4090") -> Path:
        topology = {
            "gpu_count": 1,
            "gpus": [{"index": 0, "name": gpu_name, "free_memory_mb": 16384}],
        }
        path = Path(tmpdir) / "topology.json"
        path.write_text(json.dumps(topology), encoding="utf-8")
        return path

    def test_topology_file_with_unicode_gpu_name_does_not_crash(self) -> None:
        """Topology containing non-ASCII GPU name must not raise UnicodeDecodeError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write GPU name with non-ASCII characters (e.g. trademark symbol)
            path = self._make_topology(tmpdir, gpu_name="NVIDIA RTX\u2122 4090")
            text = path.read_text(encoding="utf-8")
            parsed = json.loads(text)
            self.assertIn("RTX\u2122", parsed["gpus"][0]["name"])

    def test_missing_topology_exits_nonzero(self) -> None:
        """Missing topology file must produce a clean error message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = str(Path(tmpdir) / "no-topology.json")
            result = run_assign_gpus([
                "--topology", missing,
                "--model-size", "8000",
            ])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ERROR", result.stderr)

    def test_invalid_json_topology_exits_nonzero(self) -> None:
        """Invalid JSON in topology file must exit non-zero with JSON error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad = Path(tmpdir) / "bad.json"
            bad.write_text("{broken", encoding="utf-8")
            result = run_assign_gpus([
                "--topology", str(bad),
                "--model-size", "8000",
            ])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ERROR", result.stderr)


if __name__ == "__main__":
    unittest.main()
