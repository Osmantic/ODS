#!/usr/bin/env python3
"""Exercise the real macOS WebUI contract with streamed Compose output."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


CONTRACT = Path(__file__).with_name("test-macos-webui-optional.sh")
DOCKER_STUB = r'''#!/usr/bin/env python3
import os
import signal
import sys

signal.signal(signal.SIGPIPE, signal.SIG_DFL)
args = sys.argv[1:]
if args == ["compose", "version"]:
    print("Docker Compose version fixture")
    sys.exit(0)
if not args or args[0] != "compose" or "config" not in args:
    sys.exit("unexpected Docker invocation")
lean = any(arg.endswith("docker-compose.gateway-only.yml") for arg in args)
pixel = any(arg.endswith("pixel-native.compose.yaml.disabled") for arg in args)
if "--images" in args:
    print("example.invalid/dashboard:test")
elif pixel:
    print("dashboard\npixel-edge")
elif lean:
    print("dashboard")
else:
    case = os.environ["WEBUI_CONTRACT_FIXTURE"]
    if case == "missing":
        print("dashboard")
    elif case == "producer-failure":
        print("open-webui", flush=True)
        print("fixture Compose producer failed", file=sys.stderr)
        sys.exit(17)
    elif case == "streamed":
        os.write(1, b"open-webui\n")
        # A valid match may arrive before the producer has finished. Exceed
        # pipe capacity so an early-exiting reader cannot escape the race.
        remaining = b"trailing-service\n" * 65536
        while remaining:
            written = os.write(1, remaining)
            remaining = remaining[written:]
    else:
        sys.exit("unknown fixture")
'''


class MacWebuiOptionalContractTests(unittest.TestCase):
    def run_contract(self, case):
        with tempfile.TemporaryDirectory(prefix="ods-webui-contract-") as tmp:
            stub = Path(tmp) / "docker"
            stub.write_text(DOCKER_STUB, encoding="utf-8")
            stub.chmod(0o755)
            env = dict(os.environ, WEBUI_CONTRACT_FIXTURE=case)
            env["PATH"] = tmp + os.pathsep + env["PATH"]
            return subprocess.run(
                ["bash", str(CONTRACT)], env=env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
            )

    def test_service_match_does_not_interrupt_compose_output(self):
        result = self.run_contract("streamed")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: Mac WebUI choice", result.stdout)

    def test_missing_service_remains_a_failure(self):
        result = self.run_contract("missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("WebUI option omitted its service", result.stderr)

    def test_failed_compose_remains_a_failure_even_after_matching_output(self):
        result = self.run_contract("producer-failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixture Compose producer failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
