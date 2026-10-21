#!/usr/bin/env python3
"""Real CLI publication races and fail-closed platform primitive checks."""

import ctypes
import errno
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ODS = Path(__file__).resolve().parents[1]
HELPER = ODS / "scripts/publish-update-backup.py"
spec = importlib.util.spec_from_file_location("publish_update_backup", HELPER)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class BackupPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ods-backup-publication-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.install = self.root / "install"
        self.install.mkdir()
        (self.install / "scripts").mkdir()
        shutil.copy2(ODS / "ods-update.sh", self.install / "ods-update.sh")
        shutil.copy2(HELPER, self.install / "scripts" / HELPER.name)
        (self.install / ".env").write_text("ODS_VERSION=2.0.0\n")
        (self.install / ".version").write_text('{"version":"2.0.0"}\n')
        (self.install / "docker-compose.base.yml").write_text("services: {}\n")
        self.home = self.root / "home"
        self.backups = self.home / ".ods/backups"
        self.backups.mkdir(parents=True)
        self.old = self.backups / "backup-old-20200101-000000"
        self.old.mkdir()
        (self.old / "sentinel").write_text("old backup")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.target = self.root / "foreign"
        self.target.mkdir()
        (self.target / "sentinel").write_text("foreign target")
        self.env = dict(os.environ, HOME=str(self.home), MAX_BACKUPS="1",
                        PATH=str(self.bin) + os.pathsep + os.environ["PATH"])

    def inject(self, mode):
        # Both old mv and new Python publication run through the actual public
        # CLI; the test injects a contender only at the final syscall boundary.
        script = f'''#!{sys.executable}
import os,pathlib,subprocess,sys
source,destination=map(pathlib.Path,sys.argv[-2:])
mode={mode!r}
if mode=='directory':
 destination.mkdir();(destination/'sentinel').write_text('foreign directory')
elif mode=='empty-directory':destination.mkdir()
elif mode=='file':destination.write_text('foreign file')
elif mode=='symlink':destination.symlink_to({str(self.target)!r},target_is_directory=True)
elif mode=='unavailable':raise SystemExit(77)
elif mode=='interrupted':
 os.kill(os.getppid(),15);raise SystemExit(78)
if pathlib.Path(sys.argv[0]).name=='mv':
 os.execv('/bin/mv',['/bin/mv',*sys.argv[1:]])
result=subprocess.run([{sys.executable!r},*sys.argv[1:]])
raise SystemExit(73 if mode=='lost-response' and result.returncode==0 else result.returncode)
'''
        for name in ("python3", "mv"):
            path = self.bin / name
            path.write_text(script)
            path.chmod(0o700)

    def backup(self):
        return subprocess.run(["bash", str(self.install / "ods-update.sh"), "backup", "race proof"],
                              env=self.env, capture_output=True, text=True, timeout=20)

    def test_normal_publication_is_flat_and_retention_still_runs(self):
        result = self.backup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        published, = self.backups.glob("backup-race proof-*")
        self.assertTrue((published / "snapshot.json").is_file())
        self.assertEqual((published / ".env").read_bytes(), (self.install / ".env").read_bytes())
        self.assertFalse(self.old.exists())
        self.assertEqual(list(self.backups.glob(".backup-*")), [])

    def assert_contender_preserved(self, mode):
        self.inject(mode)
        result = self.backup()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Backup created:", result.stdout)
        self.assertTrue((self.old / "sentinel").is_file(), "Failed publication must not prune")
        stages = list(self.backups.glob(".backup-*.tmp.*"))
        self.assertEqual(len(stages), 1, "Refused publication must preserve its staged backup")
        staged, = stages
        self.assertTrue((staged / "snapshot.json").is_file())
        self.assertEqual((staged / ".env").read_bytes(), (self.install / ".env").read_bytes())
        destination, = self.backups.glob("backup-race proof-*")
        if mode == "directory":
            self.assertEqual([p.name for p in destination.iterdir()], ["sentinel"])
            self.assertEqual((destination / "sentinel").read_text(), "foreign directory")
        elif mode == "empty-directory":
            self.assertEqual(list(destination.iterdir()), [])
        elif mode == "file":
            self.assertEqual(destination.read_text(), "foreign file")
        else:
            self.assertTrue(destination.is_symlink())
            self.assertEqual(list(self.target.iterdir()), [self.target / "sentinel"])
        self.assertEqual(list(self.backups.glob(".backup-*.lock")), [])

    def test_concurrent_directory_is_never_a_parent(self):
        self.assert_contender_preserved("directory")

    def test_concurrent_empty_directory_is_not_replaced(self):
        self.assert_contender_preserved("empty-directory")

    def test_concurrent_file_is_not_replaced(self):
        self.assert_contender_preserved("file")

    def test_concurrent_symlink_is_not_followed(self):
        self.assert_contender_preserved("symlink")

    def test_unknown_publication_result_preserves_completed_backup(self):
        self.inject("lost-response")
        result = self.backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Backup created:", result.stdout)
        published, = self.backups.glob("backup-race proof-*")
        self.assertTrue((published / "snapshot.json").is_file())
        self.assertTrue((self.old / "sentinel").is_file())
        self.assertEqual(list(self.backups.glob(".backup-*.lock")), [])

    def test_unavailable_helper_or_interruption_preserves_private_staging(self):
        for mode in ("unavailable", "interrupted"):
            with self.subTest(mode=mode):
                self.inject(mode)
                result = self.backup()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Backup created:", result.stdout)
                self.assertEqual(list(self.backups.glob("backup-race proof-*")), [])
                self.assertTrue((self.old / "sentinel").is_file())
                self.assertTrue(all((p / "snapshot.json").is_file() for p in self.backups.glob(".backup-*.tmp.*")))
                self.assertEqual(len(list(self.backups.glob(".backup-*.tmp.*"))), 1 if mode == "unavailable" else 2)

    def test_unsupported_primitive_preserves_both_paths(self):
        source = self.backups / ".backup-unit.tmp.test"
        destination = self.backups / "backup-unit"
        source.mkdir(mode=0o700)
        (source / "sentinel").write_text("staged")
        with patch.object(publisher.ctypes, "CDLL", return_value=types.SimpleNamespace()):
            with self.assertRaises(OSError) as error:
                publisher.publish(source, destination)
        self.assertEqual(error.exception.errno, errno.ENOTSUP)
        self.assertEqual((source / "sentinel").read_text(), "staged")
        self.assertFalse(destination.exists())

    def test_platform_abi_and_failure_do_not_fall_back(self):
        for platform, symbol, flag in [("linux", "renameat2", 1), ("darwin", "renameatx_np", 4)]:
            calls = []

            def refused(*args):
                calls.append(args)
                ctypes.set_errno(errno.EEXIST)
                return -1

            with self.subTest(platform=platform), patch.object(publisher.sys, "platform", platform), \
                    patch.object(publisher.ctypes, "CDLL", return_value=types.SimpleNamespace(**{symbol: refused})), \
                    patch.object(publisher.os, "rename", side_effect=AssertionError("Unsafe fallback")):
                with self.assertRaises(FileExistsError):
                    publisher.rename_exclusive(91, "stage", "final")
                self.assertEqual(calls, [(91, b"stage", 91, b"final", flag)])
                self.assertEqual(refused.restype, ctypes.c_int)


if __name__ == "__main__":
    unittest.main(verbosity=2)
