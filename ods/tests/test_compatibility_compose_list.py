"""A malformed Compose contract must fail the actual release check command."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class ComposeContractListTests(unittest.TestCase):
    def test_invalid_lists_cannot_report_compatibility_pass(self):
        for canonical in [None, [], {}, False, "compose.yml"]:
            with self.subTest(canonical=canonical):
                result = self.run_gate(canonical)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn("compatibility check complete", result.stdout)

    def test_valid_multiple_contracts_and_missing_file(self):
        self.assertEqual(self.run_gate(["base.yml", "gpu.yml"]).returncode, 0)
        self.assertNotEqual(self.run_gate(["base.yml", "missing.yml"]).returncode, 0)

    def run_gate(self, canonical):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            source = Path(__file__).resolve().parents[1] / "scripts/check-compatibility.sh"
            script = root / "scripts" / source.name
            shutil.copyfile(source, script)
            for name in ["base.yml", "gpu.yml", "workflow.json", "schema.json"]:
                (root / name).write_text("{}\n")
            (root / "ports.json").write_text('{"version":1,"ports":[8080]}')
            manifest = {
                "manifestVersion": 1, "release": {"version": "1.0.0"},
                "compatibility": {"os": {"macos": {"supported": True}}},
                "contracts": {
                    "compose": {"canonical": canonical},
                    "workflowCatalog": {"canonicalPath": "workflow.json"},
                    "extensions": {"serviceManifestSchema": "schema.json"},
                    "ports": {"canonicalPath": "ports.json"},
                },
            }
            (root / "manifest.json").write_text(json.dumps(manifest))
            return subprocess.run(["bash", str(script)], capture_output=True, text=True,
                                  timeout=10, check=False)


if __name__ == "__main__":
    unittest.main()
