"""Run the pin gate with malformed digests recorded in otherwise valid locks."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class DigestSyntaxTests(unittest.TestCase):
    def test_malformed_digests_never_bypass_pin_policy(self):
        for surface in ["compose", "dockerfile", "library"]:
            for digest in ["", "a" * 63, "a" * 65, "G" * 64, "A" * 64, "a" * 64 + "@extra"]:
                with self.subTest(surface=surface, digest=digest):
                    result = self.run_gate(surface, digest)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn("digest", result.stderr)

    def test_valid_digest_pins_keep_existing_exemptions(self):
        for surface in ["compose", "dockerfile", "library"]:
            with self.subTest(surface=surface):
                result = self.run_gate(surface, "abcdef0123456789" * 4)
                self.assertEqual(result.returncode, 0, result.stderr)

    def run_gate(self, surface, digest):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "config").mkdir()
            script = root / "scripts/check-dependency-pins.py"
            shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / script.name, script)
            relative = {"compose": "docker-compose.yml", "dockerfile": "extensions/services/demo/Dockerfile",
                        "library": "extensions/library/services/demo/compose.yaml"}[surface]
            value = "example/app:" + ("sha-abcdef0" if surface == "library" else "latest") + "@sha256:" + digest
            image_file = root / relative
            image_file.parent.mkdir(parents=True, exist_ok=True)
            image_file.write_text(f"FROM {value}\n" if surface == "dockerfile" else f"services:\n  app:\n    image: {value}\n")
            lock = {"version": 1, "entries": [] if surface == "library" else [
                {"id": "demo", "path": relative, "value": value}
            ], "allow_latest": [], "allow_local_images": [], "allow_variable_refs": []}
            (root / "config/dependency-lock.json").write_text(json.dumps(lock))
            return subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                                  timeout=10, check=False)


if __name__ == "__main__":
    unittest.main()
