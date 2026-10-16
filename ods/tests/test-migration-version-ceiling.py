#!/usr/bin/env python3
"""Run the shipped migration command and scripts against private install trees."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ODS = Path(__file__).resolve().parents[1]


class MigrationCeilingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ods-migration-ceiling-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.install = self.root / "install"
        self.data = self.root / "state"
        (self.install / "scripts").mkdir(parents=True)
        self.data.mkdir()
        self.command = self.install / "scripts/migrate-config.sh"
        shutil.copyfile(ODS / "scripts/migrate-config.sh", self.command)
        shutil.copytree(ODS / "migrations", self.install / "migrations")
        self.original_env = "OPERATOR_SETTING=preserve\n"
        (self.install / ".env").write_text(self.original_env)
        self.environment = dict(os.environ, INSTALL_DIR=str(self.install), DATA_DIR=str(self.data))

    def configure(self, current, previous, *, json_version=False):
        (self.install / ".version").write_text(json.dumps({"version": current}) if json_version else current)
        (self.data / ".migration-state").write_text(previous)

    def invoke(self, action, expected=0):
        result = subprocess.run(["bash", str(self.command), action], env=self.environment,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        self.assertEqual(result.returncode, expected, result.stdout)
        return result.stdout

    def test_check_excludes_future_scripts_from_the_plan(self):
        self.configure("0.2.0", "0.1.0")
        output = self.invoke("check", expected=2)
        self.assertIn("  - 0.2.0:", output)
        self.assertNotIn("  - 2.4.1:", output)
        self.assertEqual((self.install / ".env").read_text(), self.original_env)

    def test_migrate_runs_the_target_but_does_not_backfill_future_settings(self):
        self.configure("0.2.0", "0.1.0")
        output = self.invoke("migrate")
        value = (self.install / ".env").read_text()
        self.assertIn("ENABLE_VOICE=true", value)
        self.assertIn("VOICE_PROFILE=voice", value)
        self.assertNotIn("SHIELD_API_KEY", value)
        self.assertNotIn("Running migration: 2.4.1", output)
        self.assertEqual((self.data / ".migration-state").read_text().strip(), "0.2.0")
        backups = list((self.data / "backups").glob("config-*/.env"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), self.original_env)

    def test_future_only_scripts_do_not_mutate_configuration(self):
        self.configure("2.4.0", "0.2.0", json_version=True)
        output = self.invoke("migrate")
        self.assertNotIn("Running migration:", output)
        self.assertEqual((self.install / ".env").read_text(), self.original_env)
        self.assertEqual((self.data / ".migration-state").read_text().strip(), "2.4.0")

    def test_later_upgrade_runs_the_previously_excluded_migration_once(self):
        self.configure("0.2.0", "0.1.0")
        self.invoke("migrate")
        self.configure("2.4.1", "0.2.0")
        output = self.invoke("migrate")
        self.assertIn("Running migration: 2.4.1", output)
        self.assertNotIn("Running migration: 0.2.0", output)
        value = (self.install / ".env").read_text()
        self.assertRegex(value, r"SHIELD_API_KEY=[0-9a-f]{64}")
        self.assertIn("No migration needed", self.invoke("check"))
        self.invoke("migrate")
        self.assertEqual((self.install / ".env").read_text(), value)

    def test_downgrade_never_runs_forward_migrations(self):
        self.configure("0.2.0", "2.4.1")
        self.invoke("migrate")
        self.assertEqual((self.install / ".env").read_text(), self.original_env)
        self.assertEqual((self.data / ".migration-state").read_text().strip(), "2.4.1")

    def test_invalid_present_version_cannot_authorize_migration_or_report_success(self):
        for malformed in ("broken", "2.broken.0", "", "2..0", "2.5.0-", "2.5.0+", "2.5.0.1"):
            for field in ("current", "previous"):
                for action in ("check", "migrate"):
                    with self.subTest(version=malformed, field=field, action=action):
                        current = malformed if field == "current" else "0.2.0"
                        previous = malformed if field == "previous" else "0.1.0"
                        self.configure(current, previous)
                        output = self.invoke(action, expected=1)
                        self.assertIn("Invalid version", output)
                        self.assertNotIn("Running migration:", output)
                        self.assertNotIn("No migration needed", output)
                        self.assertEqual((self.install / ".env").read_text(), self.original_env)
                        self.assertEqual((self.install / ".version").read_text(), current)
                        self.assertEqual((self.data / ".migration-state").read_text(), previous)
                        self.assertFalse((self.data / "backups").exists())

    def test_absent_versions_remain_a_valid_empty_install(self):
        self.assertIn("No migration needed", self.invoke("check"))
        self.assertEqual((self.install / ".env").read_text(), self.original_env)
        self.assertFalse((self.data / ".migration-state").exists())

    def test_release_core_suffixes_and_decimal_components_use_the_target_ceiling(self):
        for current in ("v0.2.0-rc1", "v0.2.0-12-g975fd429b", "v0.2.0+build5", "0.02.0"):
            with self.subTest(current=current):
                self.configure(current, "0.1.0", json_version=True)
                (self.install / ".env").write_text(self.original_env)
                output = self.invoke("check", expected=2)
                self.assertIn("  - 0.2.0:", output)
                self.assertNotIn("  - 2.4.1:", output)
                output = self.invoke("migrate")
                self.assertIn("Running migration: 0.2.0", output)
                self.assertNotIn("Running migration: 2.4.1", output)
                self.assertIn("ENABLE_VOICE=true", (self.install / ".env").read_text())
                self.assertEqual((self.data / ".migration-state").read_text(), current + "\n")
                self.assertIn("No migration needed", self.invoke("check"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
