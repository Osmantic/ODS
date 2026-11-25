#!/usr/bin/env python3
"""Run both complete preflights with inactive shims and Compose v1/v2."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCKER = """#!/bin/bash
[[ "$SHIM_MODE" == dead ]] && exit 1
case "$1" in
  --version) echo 'Docker version 29.8.2, build test' ;;
  info) echo '[]' ;;
  compose)
    [[ "$SHIM_MODE" == v2 && "${2:-}" == version ]] || exit 1
    echo 'Docker Compose version v2.29.0' ;;
  *) exit 125 ;;
esac
"""
COMPOSE = """#!/bin/bash
[[ "$SHIM_MODE" == v1 && "${1:-}" == version ]] || exit 1
if [[ "${2:-}" == --short ]]; then
  echo '1.29.2'
else
  echo 'docker-compose version 1.29.2, build test'
fi
"""


class DockerShimPreflightTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ods-preflight-shims-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.assertEqual(self.base.parent, Path(tempfile.gettempdir()).resolve())
        self.root = self.base / "ods"
        self.root.mkdir()
        (self.root / "scripts").mkdir()
        shutil.copy2(ROOT / "ods-preflight.sh", self.root)
        shutil.copy2(
            ROOT / "scripts/linux-install-preflight.sh", self.root / "scripts"
        )
        shutil.copytree(ROOT / "lib", self.root / "lib")
        self.bin = self.base / "bin"
        self.bin.mkdir()
        for name, source in {
            "docker": DOCKER,
            "docker-compose": COMPOSE,
            "curl": "#!/bin/bash\nexit 7\n",
            "nvidia-smi": "#!/bin/bash\nexit 1\n",
        }.items():
            path = self.bin / name
            path.write_text(source, encoding="utf-8")
            path.chmod(0o755)

    def run_preflight(self, mode, *args):
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "SHIM_MODE": mode,
            "ODS_ROOT": str(self.root),
            "ODS_ASSUME_ROOTLESS": "0",
            "MIN_DISK_GB_FREE": "0",
            "GPU_BACKEND": "nvidia",
        }
        return subprocess.run(
            ["bash", str(self.root / args[0]), *args[1:]],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=30,
        )

    def test_service_preflight_finishes_with_dead_and_working_shims(self):
        for mode in ("dead", "v1", "v2"):
            with self.subTest(mode=mode):
                result = self.run_preflight(mode, "ods-preflight.sh")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("[8/8]", result.stdout)
                self.assertIn("Pre-flight Summary", result.stdout)
                if mode != "dead":
                    self.assertIn("Docker installed: 29.8.2", result.stdout)
                    self.assertIn("Docker daemon running", result.stdout)
                    self.assertIn("Docker Compose available:", result.stdout)

    def test_install_preflight_finishes_and_writes_json(self):
        for mode in ("dead", "v1", "v2"):
            with self.subTest(mode=mode):
                output = self.base / f"{mode}.json"
                result = self.run_preflight(
                    mode, "scripts/linux-install-preflight.sh",
                    "--json-file", str(output),
                )
                self.assertEqual(
                    result.returncode, 1 if mode == "dead" else 0, result.stderr
                )
                self.assertTrue(output.is_file(), result.stderr)
                report = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(report, json.loads(result.stdout))
                self.assertEqual(report["kind"], "linux-install-preflight")
                checks = {check["id"]: check for check in report["checks"]}
                expected = "fail" if mode == "dead" else "pass"
                for name in ("DOCKER_DAEMON", "COMPOSE_CLI"):
                    self.assertEqual(checks[name]["status"], expected)
                for name in ("DISK_SPACE", "CGROUP_V2"):
                    self.assertIn(name, checks)
                self.assertEqual(report["summary"]["exit_ok"], mode != "dead")


if __name__ == "__main__":
    unittest.main()
