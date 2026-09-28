#!/usr/bin/env python3
"""Regression test: migrate-config.sh diff supports exported variables without false positives."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MIGRATE_SCRIPT = ROOT / "scripts" / "migrate-config.sh"


class TestMigrateConfigExportDiff(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.work_dir = Path(self.temp_dir.name)

        self.install_dir = self.work_dir / "install"
        self.install_dir.mkdir(parents=True)
        self.scripts_dir = self.install_dir / "scripts"
        self.scripts_dir.mkdir(parents=True)
        shutil.copy2(MIGRATE_SCRIPT, self.scripts_dir / "migrate-config.sh")

        (self.install_dir / ".version").write_text("1.0.0\n")

    def test_diff_recognizes_exported_variables(self):
        example_env = self.install_dir / ".env.example"
        example_env.write_text("PORT=8080\nNEW_VAR=hello\n")

        current_env = self.install_dir / ".env"
        current_env.write_text("export PORT=8080\n")

        env = os.environ.copy()
        env["INSTALL_DIR"] = str(self.install_dir)
        env["DATA_DIR"] = str(self.work_dir / ".ods")

        res = subprocess.run(
            ["bash", str(self.scripts_dir / "migrate-config.sh"), "diff"],
            cwd=str(self.install_dir),
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"diff failed: {res.stderr}\n{res.stdout}")

        # PORT should not appear as a new variable or deprecated variable
        self.assertIn("+ NEW_VAR=hello", res.stdout)
        self.assertNotIn("+ PORT=", res.stdout)
        self.assertNotIn("- export PORT", res.stdout)
        self.assertNotIn("- PORT", res.stdout)


if __name__ == "__main__":
    unittest.main()
