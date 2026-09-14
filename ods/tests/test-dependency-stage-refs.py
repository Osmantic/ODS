#!/usr/bin/env python3
"""Run the release checker on multistage Dockerfiles and real image controls."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check-dependency-pins.py"


class StageReferences(unittest.TestCase):
    def test_release_gate_distinguishes_stages_and_images(self):
        cases = [
            ("previous stage", "FROM example/runtime:1.0 AS build\nFROM build AS final\n", True),
            ("scratch", "FROM scratch\n", True),
            ("chained stages", "FROM example/runtime:1.0 AS build\nFROM build AS middle\nFROM middle\n", True),
            ("platform stage", "FROM example/runtime:1.0 AS build\nFROM --platform=linux/amd64 build\n", True),
            ("undeclared image", "FROM example/runtime:1.0 AS build\nFROM missing\n", False),
            ("forward alias", "FROM later\nFROM example/runtime:1.0 AS later\n", False),
            ("self alias", "FROM build AS build\n", False),
        ]
        for name, dockerfile, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "scripts").mkdir()
                (root / "config").mkdir()
                target = root / "scripts/check-dependency-pins.py"
                shutil.copy2(SCRIPT, target)
                relative = "extensions/services/example/Dockerfile"
                path = root / relative
                path.parent.mkdir(parents=True)
                path.write_text(dockerfile, encoding="utf-8")
                entries = []
                if "example/runtime:1.0" in dockerfile:
                    entries.append({"id": "runtime", "path": relative, "value": "example/runtime:1.0"})
                (root / "config/dependency-lock.json").write_text(json.dumps({
                    "version": 1, "entries": entries, "allow_latest": [],
                    "allow_local_images": [], "allow_variable_refs": [],
                }), encoding="utf-8")
                result = subprocess.run([sys.executable, str(target)], text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0 if expected else 1, result.stdout + result.stderr)
                if not expected:
                    self.assertIn("image ref is not recorded", result.stderr)


if __name__ == "__main__":
    unittest.main()
