"""Exercise compressed-backup verification through the public CLI and real tar."""

import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest


ODS = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "posix", "requires POSIX shell and process groups")
class CompressedBackupVerification(unittest.TestCase):
    def setUp(self):
        for command in ("bash", "rsync", "jq", "tar"):
            self.assertIsNotNone(shutil.which(command), f"required test tool: {command}")
        temporary = tempfile.TemporaryDirectory(prefix="ods-backup-verify-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.install = self.root / "private install"
        self.scratch = self.root / "verification temp"
        self.scratch.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for relative in (
            "ods-cli", "ods-backup.sh", "lib/service-registry.sh", "lib/rsync.sh",
            "lib/backup-paths.sh", "lib/dashboard-private-state.sh",
            "scripts/backup-native-preflight.py", "scripts/source-update-preflight.py",
            "extensions/services/dashboard-api/env_values.py",
        ):
            target = self.install / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ODS / relative, target)
        (self.install / "ods-backup.sh").chmod(0o755)
        (self.install / "config").mkdir()
        (self.install / ".env").write_text("BACKUP_TEST_VALUE=original\n")
        (self.install / ".version").write_text("3.0.0\n")
        (self.install / "docker-compose.yml").write_text("services: {}\n")
        (self.install / "config/settings.json").write_text('{"fixture": true}\n')
        self.foreign = self.root / "foreign install"
        self.foreign.mkdir()
        (self.foreign / "sentinel").write_text("retain foreign installation\n")
        self.mutators = self.root / "unexpected-mutators"
        for command in ("docker", "sudo", "systemctl", "service", "launchctl", "curl", "wget"):
            executable = self.bin / command
            executable.write_text('#!/bin/sh\nprintf "%s\\n" "$0" >> "$MUTATOR_RECEIPT"\nexit 95\n')
            executable.chmod(0o755)
        self.env = {
            **os.environ,
            "HOME": str(self.root / "home"),
            "INSTALL_DIR": str(self.install),
            "ODS_HOME": str(self.install),
            "ODS_DIR": str(self.install),
            "ODS_INSTALL_DIR": str(self.foreign),
            "TMPDIR": str(self.scratch),
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "MUTATOR_RECEIPT": str(self.mutators),
        }
        Path(self.env["HOME"]).mkdir()
        self.addCleanup(self.assert_isolated)

    def assert_isolated(self):
        self.assertFalse(self.mutators.exists(), "test attempted a host/network mutation")
        self.assertEqual((self.foreign / "sentinel").read_text(), "retain foreign installation\n")
        self.assertEqual(sorted(path.name for path in self.foreign.iterdir()), ["sentinel"])

    def cli(self, *arguments, **kwargs):
        env = {**self.env, **kwargs.pop("environment", {})}
        return subprocess.run(
            ["bash", str(self.install / "ods-cli"), "backup", *arguments],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=20, **kwargs,
        )

    def create_backup(self, compressed=True):
        arguments = ["--type", "config"] + (["--compress"] if compressed else [])
        result = self.cli(*arguments)
        self.assertEqual(result.returncode, 0, result.stdout)
        match = re.search(r"Backup complete: ([A-Za-z0-9_-]+)", result.stdout)
        self.assertIsNotNone(match, result.stdout)
        backup_id = match.group(1)
        artifact = self.install / ".backups" / (backup_id + (".tar.gz" if compressed else ""))
        self.assertTrue(artifact.exists(), result.stdout)
        self.assertEqual(list(self.scratch.iterdir()), [])
        return backup_id, artifact

    def test_created_archive_verifies_by_reported_id_and_filename(self):
        backup_id, archive = self.create_backup()
        original = archive.read_bytes()
        for identifier in (backup_id, archive.name):
            with self.subTest(identifier=identifier):
                result = self.cli("verify", identifier)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn("Backup verified:", result.stdout)
                self.assertEqual(list(self.scratch.iterdir()), [])
                self.assertEqual(archive.read_bytes(), original)
                self.assertFalse((archive.parent / backup_id).exists())

    def test_rejected_archives_remove_extracted_payload(self):
        backup_id, archive = self.create_backup()
        original = archive.read_bytes()
        builder = self.root / "archive builder"
        builder.mkdir()
        subprocess.run(["tar", "xzf", str(archive), "-C", str(builder)], check=True)
        extracted = builder / backup_id
        (extracted / ".env").write_text("BACKUP_TEST_VALUE=changed\n")
        subprocess.run(["tar", "czf", str(archive), "-C", str(builder), backup_id], check=True)
        damaged = {"checksum mismatch": archive.read_bytes()}
        (extracted / "checksums.sha256").unlink()
        subprocess.run(["tar", "czf", str(archive), "-C", str(builder), backup_id], check=True)
        damaged["missing checksums"] = archive.read_bytes()
        damaged["truncated gzip"] = original[:-16]
        for index, (label, payload) in enumerate(damaged.items()):
            with self.subTest(damage=label):
                case_scratch = self.root / f"damage temp {index}"
                case_scratch.mkdir()
                archive.write_bytes(payload)
                result = self.cli("verify", archive.name, environment={"TMPDIR": str(case_scratch)})
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn("Backup verified:", result.stdout)
                self.assertEqual(list(case_scratch.iterdir()), [], "rejected payload remains extracted")
                self.assertEqual(archive.read_bytes(), payload)

    def test_directory_priority_and_checksum_refusal_are_preserved(self):
        backup_id, directory = self.create_backup(compressed=False)
        archive = directory.with_name(backup_id + ".tar.gz")
        archive.write_bytes(b"invalid sibling archive")
        result = self.cli("verify", backup_id)
        self.assertEqual(result.returncode, 0, result.stdout)
        (directory / ".env").write_text("BACKUP_TEST_VALUE=changed\n")
        refused = self.cli("verify", backup_id)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertNotIn("Backup verified:", refused.stdout)
        self.assertEqual(archive.read_bytes(), b"invalid sibling archive")
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_missing_and_invalid_identifiers_do_not_stage_data(self):
        for identifier in ("missing", "missing.tar.gz", "../foreign install"):
            with self.subTest(identifier=identifier):
                result = self.cli("verify", identifier)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn("Backup verified:", result.stdout)
                self.assertEqual(list(self.scratch.iterdir()), [])

    def test_terminated_verification_removes_its_private_staging(self):
        self.assert_interrupted_cleanup(signal.SIGTERM)

    def test_interrupted_verification_removes_its_private_staging(self):
        self.assert_interrupted_cleanup(signal.SIGINT)

    def assert_interrupted_cleanup(self, interruption):
        _backup_id, archive = self.create_backup()
        original = archive.read_bytes()
        marker = self.root / "extraction-started"
        real_tar = shutil.which("tar")
        wrapper = self.bin / "tar"
        wrapper.write_text(
            '#!/bin/bash\nset -eu\n'
            'if [[ "$1" == xzf && "$*" != *checksums.sha256* ]]; then\n'
            '  printf started > "$EXTRACTION_MARKER"\n'
            '  exec sleep 30\n'
            'fi\nexec "$REAL_TAR" "$@"\n'
        )
        wrapper.chmod(0o755)
        env = {**self.env, "REAL_TAR": real_tar, "EXTRACTION_MARKER": str(marker)}
        with tempfile.TemporaryFile(mode="w+") as output:
            process = subprocess.Popen(
                ["bash", str(self.install / "ods-cli"), "backup", "verify", archive.name],
                env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 10
                while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(marker.exists(), "verification did not reach full extraction")
                # Only the process group created for this fixture receives a signal.
                os.killpg(process.pid, interruption)
                process.wait(timeout=10)
                self.assertNotEqual(process.returncode, 0)
                deadline = time.monotonic() + 3
                while list(self.scratch.iterdir()) and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual(list(self.scratch.iterdir()), [], "terminated extraction remains on disk")
                self.assertEqual(archive.read_bytes(), original)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
