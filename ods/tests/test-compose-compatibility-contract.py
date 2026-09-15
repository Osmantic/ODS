#!/usr/bin/env python3
"""Run the release compatibility entry point against isolated manifests."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "check-compatibility.sh"
BASH = shutil.which("bash")
JQ = shutil.which("jq")


@unittest.skipUnless(BASH and JQ, "Bash and jq are required")
class ComposeCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ods compatibility ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        self.script = self.root / "scripts" / SOURCE.name
        shutil.copyfile(SOURCE, self.script)
        for name in ("compose one.yml", "compose two.yml", "catalog.json", "schema.json",
                     "42", "null", "true"):
            (self.root / name).write_text("{}\n")
        (self.root / "ports.json").write_text(
            '{"version": 1, "ports": [{"name": "fixture", "port": 8080}]}\n'
        )
        self.manifest = {
            "manifestVersion": "1",
            "release": {"version": "fixture"},
            "compatibility": {"os": {"macos": {"supported": True}}},
            "contracts": {
                "compose": {"canonical": ["compose one.yml"]},
                "workflowCatalog": {"canonicalPath": "catalog.json"},
                "extensions": {"serviceManifestSchema": "schema.json"},
                "ports": {"canonicalPath": "ports.json"},
            },
        }

    def run_check(self, **env):
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))
        result = subprocess.run(
            [BASH, str(self.script)], env={**os.environ, **env},
            capture_output=True, text=True, timeout=5, check=False,
        )

        result.stdout = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
        return result

    def assert_rejected(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("[PASS] compose canonical files", result.stdout)
        self.assertNotIn("[PASS] compatibility check complete", result.stdout)

    def test_missing_compose_list_is_rejected(self):
        del self.manifest["contracts"]["compose"]["canonical"]
        self.assert_rejected(self.run_check())

    def test_empty_or_wrong_shape_is_rejected(self):
        for value in (None, [], {}, {"wrong_key": "compose one.yml"}, "compose one.yml", 7, True):
            with self.subTest(canonical=value):
                self.manifest["contracts"]["compose"]["canonical"] = value
                self.assert_rejected(self.run_check())

    def test_non_string_entries_cannot_name_existing_files(self):
        for value in (42, None, True):
            with self.subTest(entry=value):
                self.manifest["contracts"]["compose"]["canonical"] = ["compose one.yml", value]
                self.assert_rejected(self.run_check())

    def test_jq_failure_after_partial_output_is_rejected(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        wrapper = bin_dir / "jq"
        wrapper.write_text(
            "#!/bin/bash\n"
            "for argument in \"$@\"; do\n"
            "  if [[ \"$argument\" == *'.contracts.compose.canonical'* ]]; then\n"
            "    printf '%s\\n' 'compose one.yml'\n"
            "    exit 7\n"
            "  fi\n"
            "done\n"
            'exec "$REAL_JQ" "$@"\n'
        )
        wrapper.chmod(0o755)
        self.assert_rejected(self.run_check(PATH=f"{bin_dir}:{os.environ['PATH']}", REAL_JQ=JQ))

    def test_valid_compose_list_passes(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[PASS] compatibility check complete", result.stdout)

    def test_multiple_files_with_spaces_pass(self):
        self.manifest["contracts"]["compose"]["canonical"].append("compose two.yml")
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_referenced_file_still_fails(self):
        self.manifest["contracts"]["compose"]["canonical"].append("missing.yml")
        result = self.run_check()
        self.assert_rejected(result)
        self.assertIn("missing compose contract file: missing.yml", result.stdout)

    def test_empty_entry_is_rejected(self):
        self.manifest["contracts"]["compose"]["canonical"].append("")
        self.assert_rejected(self.run_check())


if __name__ == "__main__":
    unittest.main(verbosity=2)
