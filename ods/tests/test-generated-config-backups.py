#!/usr/bin/env python3
"""Exercise writer discovery after the public config backup command."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MARKER = "ODS-CONTRACT-WRITER: litellm-lemonade"


class GeneratedConfigBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ods-config-backups-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "ODS installation"
        contract = json.loads((ROOT / "config/generated-config-contracts.json").read_text())
        paths = {
            "scripts/validate-generated-configs.py", "config/generated-config-contracts.json",
            "ods-backup.sh", "lib/rsync.sh", "lib/backup-paths.sh",
        }
        for surface in contract["surfaces"]:
            paths.update(writer["path"] for writer in surface["writers"])
            paths.update(invariant["path"] for invariant in surface["invariants"])
        for relative in paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        (self.root / "data").mkdir()
        (self.root / ".version").write_text('{"version": "test"}\n')

    def validate(self):
        return subprocess.run(
            [sys.executable, str(self.root / "scripts/validate-generated-configs.py")],
            cwd=self.root, capture_output=True, text=True, timeout=30,
        )

    def test_default_config_backup_preserves_valid_contracts(self):
        before = self.validate()
        self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
        backup = subprocess.run(
            ["bash", str(self.root / "ods-backup.sh"), "--type", "config"],
            cwd=self.root, capture_output=True, text=True, timeout=60,
            env=dict(os.environ, ODS_DIR=str(self.root), TMPDIR=self.temp.name),
        )
        self.assertEqual(backup.returncode, 0, backup.stdout + backup.stderr)
        retained = list((self.root / ".backups").glob("*/config/litellm/lemonade.yaml"))
        self.assertEqual(len(retained), 1, backup.stdout)
        original = retained[0].read_bytes()
        self.assertIn(MARKER.encode(), original)
        after = self.validate()
        self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
        self.assertEqual(retained[0].read_bytes(), original)

    def test_extra_live_writer_is_still_rejected(self):
        for relative in ("scripts/new-writer.py", "extensions/services/example/new-writer.sh"):
            with self.subTest(path=relative):
                writer = self.root / relative
                writer.parent.mkdir(parents=True, exist_ok=True)
                writer.write_text(f"# {MARKER}\n")
                try:
                    result = self.validate()
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn(relative, result.stdout)
                    self.assertIn("must exactly match marked writers", result.stdout)
                finally:
                    writer.unlink()

    def test_missing_live_marker_is_still_rejected(self):
        writer = self.root / "config/litellm/lemonade.yaml"
        writer.write_text(writer.read_text().replace(MARKER, "former writer"))
        result = self.validate()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("must exactly match marked writers", result.stdout)


if __name__ == "__main__":
    unittest.main()
