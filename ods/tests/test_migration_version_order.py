"""Exercise numeric migration order and recovery through the real CLI."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate-config.sh"
VERSIONS = ["2.4.2", "2.4.10", "2.9.0", "2.10.0", "10.0.0"]


class MigrationOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.install = self.root / "install"
        self.data = self.root / "data"
        self.scripts = self.install / "scripts"
        self.migrations = self.install / "migrations"
        self.scripts.mkdir(parents=True)
        self.migrations.mkdir()
        self.data.mkdir()
        self.script = self.scripts / SCRIPT.name
        shutil.copy2(SCRIPT, self.script)
        (self.install / ".env").write_text("OPERATOR_SETTING=keep-me\n")
        (self.install / ".version").write_text("10.0.0\n")
        self.state = self.data / ".migration-state"
        self.state.write_text("2.4.1\n")
        self.applied = self.data / "applied"
        previous = "2.4.1"
        for version in [*VERSIONS, "11.0.0"]:
            (self.migrations / f"migrate-v{version}.sh").write_text(
                "#!/usr/bin/env bash\n"
                f"# Description: apply {version} after {previous}\n"
                "set -eu\n"
                f'[[ "$(cat "$DATA_DIR/.migration-state")" == "{previous}" ]]\n'
                f'[[ "${{FAIL_AT:-}}" != "{version}" ]]\n'
                f'printf "%s\\n" "{version}" >> "$DATA_DIR/applied"\n'
            )
            previous = version

    def run_cli(self, action, **extra_env):
        return subprocess.run(
            ["bash", str(self.script), action],
            env={**os.environ, "INSTALL_DIR": str(self.install),
                 "DATA_DIR": str(self.data), "FAIL_AT": "", **extra_env},
            capture_output=True, text=True, timeout=20, check=False,
        )

    def test_check_lists_numeric_order_without_applying(self):
        result = self.run_cli("check")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(re.findall(r"^  - ([0-9.]+):", result.stdout, re.MULTILINE), VERSIONS)
        self.assertFalse(self.applied.exists())
        self.assertEqual(self.state.read_text().strip(), "2.4.1")

    def test_migrate_applies_dependencies_before_later_versions(self):
        result = self.run_cli("migrate")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.applied.read_text().splitlines(), VERSIONS)
        self.assertEqual(self.state.read_text().strip(), "10.0.0")
        backups = list((self.data / "backups").glob("config-*/.env"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "OPERATOR_SETTING=keep-me\n")

    def test_failed_step_checkpoints_and_retry_does_not_replay(self):
        failed = self.run_cli("migrate", FAIL_AT="2.4.10")
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self.state.read_text().strip(), "2.4.2", failed.stdout)
        self.assertEqual(self.applied.read_text().splitlines(), ["2.4.2"])
        retry = self.run_cli("migrate")
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertEqual(self.applied.read_text().splitlines(), VERSIONS)
        self.assertEqual(self.state.read_text().strip(), "10.0.0")

    def test_numeric_sort_does_not_depend_on_dots_or_spaces_in_install_path(self):
        moved = self.root / "install.9.99 with spaces"
        self.install.rename(moved)
        self.install = moved
        self.script = moved / "scripts" / SCRIPT.name
        result = self.run_cli("migrate")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.applied.exists(), result.stdout)
        self.assertEqual(self.applied.read_text().splitlines(), VERSIONS)

    def test_downgrade_remains_a_noop(self):
        self.state.write_text("11.0.0\n")
        result = self.run_cli("migrate")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.applied.exists())
        self.assertEqual(self.state.read_text().strip(), "11.0.0")


if __name__ == "__main__":
    unittest.main()
