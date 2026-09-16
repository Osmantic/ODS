#!/usr/bin/env python3
"""Run ods update through its real fallback backup before a controlled pull failure."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ODS = Path(__file__).resolve().parents[1]


class LegacyUpdateBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ods-legacy-update-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.install = self.root / "install with spaces"
        self.binary = self.root / "bin"
        self.binary.mkdir()
        for relative in ("data/open-webui", "config", "lib", "installers/lib"):
            (self.install / relative).mkdir(parents=True)
        for relative in ("ods-backup.sh", "lib/rsync.sh", "lib/backup-paths.sh",
                         "installers/lib/compose-images.sh", "docker-compose.base.yml", "manifest.json"):
            shutil.copy2(ODS / relative, self.install / relative)
        (self.install / ".compose-flags").write_text("-f docker-compose.base.yml\n")
        (self.install / ".version").write_text("2.6.0\n")
        (self.install / ".env").write_text(
            "ODS_VERSION=2.6.0\nODS_MODE=local\nGPU_BACKEND=cpu\nGPU_COUNT=1\nTIER=1\n"
            "SHIELD_API_KEY=fixture\nLLAMA_CPU_LIMIT=8.0\nLLAMA_CPU_RESERVATION=2.0\n")
        self.payload = b"User data that must survive the failed image pull.\n"
        (self.install / "data/open-webui/notes.txt").write_bytes(self.payload)
        (self.binary / "docker").write_text("""#!/bin/bash
set -euo pipefail
if [[ "$1" == info ]]; then printf '16\\n'; exit 0; fi
if [[ "$1" == compose && " $* " == *" --format json "* ]]; then
  printf '%s\\n' '{"services":{"fixture":{"image":"fixture.invalid/ods:test"}}}'
  exit 0
fi
if [[ "$1" == pull ]]; then echo 'fixture pull stopped after backup' >&2; exit 2; fi
echo "Unexpected fixture Docker command: $*" >&2
exit 91
""")
        (self.binary / "docker").chmod(0o755)
        self.env = dict(os.environ, ODS_HOME=str(self.install), HOME=str(self.root),
                        PATH=f"{self.binary}:{os.environ['PATH']}", NO_COLOR="1")
        self.env.pop("ODS_DIR", None)

    def update(self):
        result = subprocess.run(["bash", str(ODS / "ods-cli"), "update", "--force"],
                                env=self.env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0, "The fixture intentionally rejects image pulls")
        self.assertIn("fixture pull stopped after backup", result.stdout + result.stderr)
        return result.stdout + result.stderr

    def test_fallback_creates_restorable_user_data_with_a_description(self):
        output = self.update()
        manifests = list((self.install / ".backups").glob("*/manifest.json"))
        self.assertEqual(len(manifests), 1, output)
        manifest = json.loads(manifests[0].read_text())
        self.assertEqual(manifest["backup_type"], "user-data")
        self.assertRegex(manifest["description"], r"^pre-update-\d{8}-\d{6}$")
        backup = manifests[0].parent
        self.assertEqual((backup / "data/open-webui/notes.txt").read_bytes(), self.payload)
        self.assertEqual((self.install / "data/open-webui/notes.txt").read_bytes(), self.payload)
        self.assertNotIn("Pre-update backup failed", output)

        restored = self.root / "restored"
        (restored / "lib").mkdir(parents=True)
        (restored / "data").mkdir()
        for helper in ("rsync.sh", "backup-paths.sh"):
            shutil.copy2(ODS / "lib" / helper, restored / "lib" / helper)
        shutil.copytree(backup, restored / ".backups" / backup.name)
        restore_env = dict(self.env, ODS_DIR=str(restored))
        result = subprocess.run(["bash", str(ODS / "ods-restore.sh"), "-f", backup.name],
                                env=restore_env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((restored / "data/open-webui/notes.txt").read_bytes(), self.payload)

    def test_preferred_snapshot_script_keeps_its_existing_label_contract(self):
        snapshot = self.install / "ods-update.sh"
        snapshot.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$(dirname "$0")/snapshot-args"\n')
        snapshot.chmod(0o755)
        self.update()
        arguments = (self.install / "snapshot-args").read_text().splitlines()
        self.assertEqual(arguments[0], "backup")
        self.assertRegex(arguments[1], r"^pre-update-\d{8}-\d{6}$")
        self.assertFalse((self.install / ".backups").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
