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
        # VOICE_PROFILE was a dead key: nothing consumes it and it is not a
        # schema property, so writing it made `ods env validate` fail with an
        # unknown-key error on every migrated install.
        self.assertNotIn("VOICE_PROFILE", value)
        self.assertNotIn("SHIELD_API_KEY", value)
        self.assertNotIn("Running migration: 2.4.1", output)
        self.assertEqual((self.data / ".migration-state").read_text().strip(), "0.2.0")
        backups = list((self.data / "backups").glob("config-*/.env"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), self.original_env)

    def test_migrations_only_write_schema_known_env_keys(self):
        schema = json.loads((ODS / ".env.schema.json").read_text())
        known_keys = set(schema["properties"])
        for migration in sorted((ODS / "migrations").glob("migrate-v*.sh")):
            with self.subTest(migration=migration.name):
                env_file = self.install / ".env"
                env_file.write_text(self.original_env)
                before = {
                    line.split("=", 1)[0]
                    for line in env_file.read_text().splitlines()
                    if "=" in line
                }
                subprocess.run(
                    ["bash", str(migration)],
                    env=self.environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=20,
                    check=True,
                )
                after = {
                    line.split("=", 1)[0]
                    for line in env_file.read_text().splitlines()
                    if "=" in line
                }
                self.assertEqual(after - before - known_keys, set())

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
