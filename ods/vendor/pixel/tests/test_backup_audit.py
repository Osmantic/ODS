import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDITOR = ROOT / "scripts" / "audit-private-backup.py"
RESTORE_ROOT = "var/lib/pixel-test"


def member(name: str, kind: bytes = tarfile.REGTYPE, data: bytes = b"", *, linkname: str = "", mode: int = 0o600):
    item = tarfile.TarInfo(name)
    item.type = kind
    item.mode = mode
    item.linkname = linkname
    item.size = len(data) if kind == tarfile.REGTYPE else 0
    return item, io.BytesIO(data) if kind == tarfile.REGTYPE else None


def archive_bytes(entries, paths=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for item, payload in entries:
            archive.addfile(item, payload)
        manifest = json.dumps({"schemaVersion": 1, "pixelVersion": "test", "paths": paths or [RESTORE_ROOT]}).encode()
        info = tarfile.TarInfo(".pixel-backup-manifest.json")
        info.mode = 0o600
        info.size = len(manifest)
        archive.addfile(info, io.BytesIO(manifest))
    return output.getvalue()


class PrivateBackupAuditTests(unittest.TestCase):
    def run_audit(self, entries, paths=None):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            allowed = directory / "allowed"
            manifest = directory / "manifest"
            allowed.write_text(RESTORE_ROOT + "\n", encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(AUDITOR), "--allowed", str(allowed), "--manifest-output", str(manifest)],
                input=archive_bytes(entries, paths), capture_output=True, check=False,
            )

    def valid_entries(self):
        return [
            member(RESTORE_ROOT, tarfile.DIRTYPE, mode=0o700),
            member(f"{RESTORE_ROOT}/state.json", data=b'{}\n'),
            member(f"{RESTORE_ROOT}/current", tarfile.SYMTYPE, linkname="state.json", mode=0o700),
        ]

    def test_accepts_bounded_archive_with_internal_symlink(self):
        result = self.run_audit(self.valid_entries())
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(json.loads(result.stdout)["status"], "pass")

    def test_rejects_symbolic_link_restore_root(self):
        result = self.run_audit([member(RESTORE_ROOT, tarfile.SYMTYPE, linkname="pixel-test")])
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_symlink_escape(self):
        entries = self.valid_entries()[:2] + [member(f"{RESTORE_ROOT}/escape", tarfile.SYMTYPE, linkname="../../../etc")]
        self.assertNotEqual(self.run_audit(entries).returncode, 0)

    def test_rejects_control_character_symlink(self):
        entries = self.valid_entries()[:2] + [member(f"{RESTORE_ROOT}/bad", tarfile.SYMTYPE, linkname="bad\nname")]
        self.assertNotEqual(self.run_audit(entries).returncode, 0)

    def test_rejects_hardlink(self):
        entries = self.valid_entries()[:2] + [member(f"{RESTORE_ROOT}/hard", tarfile.LNKTYPE, linkname=f"{RESTORE_ROOT}/state.json")]
        self.assertNotEqual(self.run_audit(entries).returncode, 0)

    def test_rejects_path_traversal(self):
        entries = self.valid_entries() + [member("../escape", data=b"bad")]
        self.assertNotEqual(self.run_audit(entries).returncode, 0)

    def test_rejects_member_outside_declared_root(self):
        entries = self.valid_entries() + [member("etc/pixel-escape", data=b"bad")]
        self.assertNotEqual(self.run_audit(entries).returncode, 0)

    def test_rejects_duplicate_member(self):
        entries = self.valid_entries() + [member(f"{RESTORE_ROOT}/state.json", data=b"again")]
        self.assertNotEqual(self.run_audit(entries).returncode, 0)

    def test_rejects_setuid_member(self):
        entries = self.valid_entries() + [member(f"{RESTORE_ROOT}/privileged", data=b"bad", mode=0o4755)]
        self.assertNotEqual(self.run_audit(entries).returncode, 0)

    def test_accepts_setgid_directory(self):
        entries = [
            member(RESTORE_ROOT, tarfile.DIRTYPE, mode=0o2750),
            member(f"{RESTORE_ROOT}/state.json", data=b'{}\n'),
        ]
        self.assertEqual(self.run_audit(entries).returncode, 0)

    def test_rejects_setgid_non_directory(self):
        entries = self.valid_entries() + [member(f"{RESTORE_ROOT}/group-executable", data=b"bad", mode=0o2755)]
        self.assertNotEqual(self.run_audit(entries).returncode, 0)


if __name__ == "__main__":
    unittest.main()
