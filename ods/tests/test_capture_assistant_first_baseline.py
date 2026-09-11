#!/usr/bin/env python3
"""Tests for the read-only Assistant-First baseline receipt."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "capture-assistant-first-baseline.py"


def _executable(path: Path, source: str) -> Path:
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class BaselineReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.install = self.base / "install"
        self.install.mkdir()
        (self.install / "docker-compose.base.yml").write_text("services: {}\n", encoding="utf-8")
        (self.install / "docker-compose.cpu.yml").write_text("services: {}\n", encoding="utf-8")
        (self.install / ".compose-flags").write_text(
            "-f docker-compose.base.yml -f docker-compose.cpu.yml\n", encoding="utf-8"
        )
        self.log = self.base / "docker.log"
        self.docker = _executable(
            self.base / "docker",
            r'''
            #!/usr/bin/env python3
            import json, os, sys
            args = sys.argv[1:]
            missing = os.environ.get("FAKE_EVIDENCE_MISSING") == "1"
            with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as handle:
                handle.write(json.dumps(args) + "\n")
            if args[0] == "compose" and "config" in args:
                print(json.dumps({"services": {
                    "voice": {"image": "example/voice:1", "environment": {"TOKEN": "must-not-leak"}},
                    "dashboard": {"image": "example/dashboard:1"},
                    "duplicate": {"image": "example/dashboard:1"}
                }}))
            elif args[0] == "compose" and "ps" in args and not missing:
                print("a" * 12)
                print("b" * 12)
            elif args[:2] == ["image", "inspect"] and not missing:
                print("100" if args[-1] == "example/dashboard:1" else "200")
            elif args[0] == "stats":
                print(json.dumps({"CPUPerc": "1.25%", "MemUsage": "1MiB / 2MiB"}))
                print(json.dumps({"CPUPerc": "2.50%", "MemUsage": "512KiB / 1MiB"}))
            else:
                sys.exit(9)
            ''',
        )
        self.git = _executable(
            self.base / "git",
            """
            #!/usr/bin/env python3
            import os, sys
            if os.environ.get("FAKE_EVIDENCE_MISSING") == "1":
                sys.exit(1)
            print("deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
            """,
        )
        self.du = _executable(
            self.base / "du",
            """
            #!/usr/bin/env python3
            import os, sys
            if os.environ.get("FAKE_EVIDENCE_MISSING") == "1":
                sys.exit(1)
            print("4096\\tfixture")
            """,
        )
        self.curl = _executable(
            self.base / "curl",
            """
            #!/usr/bin/env python3
            print("204", end="")
            """,
        )
        self.env = {
            **os.environ,
            "FAKE_DOCKER_LOG": str(self.log),
            "ODS_MEASURE_DOCKER": str(self.docker),
            "ODS_MEASURE_GIT": str(self.git),
            "ODS_MEASURE_DU": str(self.du),
            "ODS_MEASURE_CURL": str(self.curl),
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(self.install),
                "--profile",
                "core",
                "--inference-route",
                "local-cpu",
                "--output",
                str(self.base / "receipt.json"),
                *extra,
            ],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

    def test_receipt_is_sorted_bounded_and_secret_free(self) -> None:
        result = self._run(
            "--startup-to-ready-seconds",
            "4.5",
            "--assistant-ready-url",
            "http://127.0.0.1:3001/health",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        raw = (self.base / "receipt.json").read_text(encoding="utf-8")
        self.assertNotIn("must-not-leak", raw)
        self.assertNotIn("environment", raw)
        self.assertNotIn("127.0.0.1", raw)
        receipt = json.loads(raw)
        self.assertEqual(receipt["selection"]["composeFiles"], ["docker-compose.base.yml", "docker-compose.cpu.yml"])
        self.assertEqual(receipt["resolved"]["services"], ["dashboard", "duplicate", "voice"])
        self.assertEqual(receipt["resolved"]["images"], ["example/dashboard:1", "example/voice:1"])
        self.assertEqual(receipt["footprint"]["installBytes"], 4096)
        self.assertEqual(receipt["footprint"]["localImageBytes"], 300)
        self.assertEqual(receipt["footprint"]["localImageCount"], 2)
        self.assertEqual(receipt["footprint"]["missingImageCount"], 0)
        self.assertEqual(receipt["footprint"]["runningContainerCount"], 2)
        self.assertEqual(receipt["idleSample"]["cpuPercent"], 3.75)
        self.assertEqual(receipt["idleSample"]["memoryBytes"], 1572864)
        self.assertEqual(receipt["startupToReadySeconds"], 4.5)
        self.assertEqual(receipt["assistantReadiness"], {"attempted": True, "ready": True, "statusCode": 204})

        calls = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        forbidden = {"build", "create", "down", "kill", "pull", "remove", "restart", "rm", "run", "start", "stop", "up", "update"}
        self.assertTrue(calls)
        self.assertFalse(any(forbidden.intersection(call) for call in calls))

    def test_explicit_compose_files_override_saved_flags(self) -> None:
        result = self._run("--compose-file", "docker-compose.cpu.yml")
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads((self.base / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["selection"]["composeFiles"], ["docker-compose.cpu.yml"])

    def test_unavailable_runtime_evidence_is_explicit(self) -> None:
        self.env["FAKE_EVIDENCE_MISSING"] = "1"
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads((self.base / "receipt.json").read_text(encoding="utf-8"))
        self.assertIsNone(receipt["source"]["gitHead"])
        self.assertIsNone(receipt["footprint"]["installBytes"])
        self.assertEqual(receipt["footprint"]["localImageBytes"], 0)
        self.assertEqual(receipt["footprint"]["localImageCount"], 0)
        self.assertEqual(receipt["footprint"]["missingImageCount"], 2)
        self.assertIsNone(receipt["footprint"]["runningContainerCount"])
        self.assertEqual(
            receipt["idleSample"],
            {"available": False, "containerCount": None, "cpuPercent": None, "memoryBytes": None},
        )
        self.assertEqual(
            receipt["assistantReadiness"],
            {"attempted": False, "ready": None, "statusCode": None},
        )

    def test_invalid_inputs_fail_before_measurement(self) -> None:
        missing = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False)
        self.assertNotEqual(missing.returncode, 0)
        external = self._run("--compose-file", str(self.base / "outside.yml"))
        self.assertNotEqual(external.returncode, 0)
        non_loopback = self._run("--assistant-ready-url", "https://example.com/health")
        self.assertNotEqual(non_loopback.returncode, 0)


if __name__ == "__main__":
    unittest.main()
