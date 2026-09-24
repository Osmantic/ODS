"""Validate URL authority, not just text that resembles a localhost prefix."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GoldenHealthUrlTests(unittest.TestCase):
    def test_invalid_authorities_and_ports_fail_the_cli(self):
        for url in [
            "http://127.0.0.1:80@outside.example/health",
            "http://127.0.0.1:password@outside.example/health",
            "http://127.0.0.1:/health",
            "http://127.0.0.1:zero/health",
            "http://127.0.0.1:0/health",
            "http://127.0.0.1:65536/health",
            "http://127.0.0.1:8080/health\n",
        ]:
            with self.subTest(url=url):
                result = self.run_gate(url)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("localhost HTTP", result.stdout)
                self.assertNotIn("Traceback", result.stderr)

    def test_valid_local_ports_paths_and_queries(self):
        for url in ["http://127.0.0.1:1/", "http://127.0.0.1:65535/health?verbose=true"]:
            with self.subTest(url=url):
                self.assertEqual(self.run_gate(url).returncode, 0)

    def run_gate(self, url):
        contract = json.loads((ROOT / "config/golden-paths.json").read_text())
        contract["scenarios"][0]["expected"]["health_checks"][0]["url"] = url
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "golden.json"
            source.write_text(json.dumps(contract))
            return subprocess.run([sys.executable, str(ROOT / "scripts/validate-golden-paths.py"), str(source)],
                                  capture_output=True, text=True, check=False, timeout=10)


if __name__ == "__main__":
    unittest.main()
