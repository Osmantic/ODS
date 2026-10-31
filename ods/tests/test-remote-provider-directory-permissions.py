#!/usr/bin/env python3
"""Directory traversal repair uses the same custody boundary as provider secrets."""
import ast
import logging
import os
from pathlib import Path
import stat
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "bin/ods-host-agent.py").read_text(encoding="utf-8")
node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)
            and n.name == "_repair_remote_provider_secret_permissions")


class DescriptorOS:
    """POSIX syscall simulation, including ownership and no-follow open refusals."""
    name = "posix"
    O_RDONLY = 0
    O_CLOEXEC = 1
    O_DIRECTORY = 2
    O_NOFOLLOW = 4
    O_NONBLOCK = 8

    def __init__(self, uid=1001, gid=1002):
        self.uid, self.gid = uid, gid
        self.entries = {
            9: SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=uid, st_gid=gid, st_dev=1, st_ino=9),
            10: SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=uid, st_gid=gid, st_dev=1, st_ino=10),
            11: SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=uid, st_gid=gid, st_dev=1, st_ino=11),
            12: SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=uid, st_gid=gid, st_dev=1, st_ino=12),
        }
        self.changes = []
        self.closed = []
        self.fail_chmod = False

    def geteuid(self):
        return self.uid

    def getegid(self):
        return self.gid

    def open(self, path, flags, *, dir_fd=None):
        if dir_fd is None:
            fd = 10 if Path(path).name == "remote-provider" else 9
        else:
            fd = 10 if dir_fd == 9 else 11 if path == "secrets" else 12
        metadata = self.entries[fd]
        if stat.S_ISLNK(metadata.st_mode):
            raise OSError("symlink refused")
        if flags & self.O_DIRECTORY and not stat.S_ISDIR(metadata.st_mode):
            raise OSError("not a directory")
        return fd

    def stat(self, name, **kwargs):
        if name != "provider-api-key":
            raise FileNotFoundError(name)
        return self.entries[12]

    def fstat(self, fd):
        return self.entries[fd]

    def fchown(self, fd, uid, gid):
        self.changes.append(("owner", fd, uid, gid))
        self.entries[fd].st_uid, self.entries[fd].st_gid = uid, gid

    def fchmod(self, fd, mode):
        if self.fail_chmod:
            raise OSError("permission denied")
        self.changes.append(("mode", fd, mode))
        self.entries[fd].st_mode = stat.S_IFMT(self.entries[fd].st_mode) | mode

    def close(self, fd):
        self.closed.append(fd)


def repair(syscalls, root, provider_gid=10778):
    namespace = {
        "os": syscalls, "stat_mod": stat, "logger": logging.getLogger(__name__),
        "_remote_provider_root": lambda: root,
        "_REMOTE_PROVIDER_EGRESS_GID": provider_gid,
        "_REMOTE_PROVIDER_CONTAINER_SECRET_REFS": {"REMOTE_LLM_API_KEY"},
        "_REMOTE_PROVIDER_SECRET_REF_TO_FILENAME": {"REMOTE_LLM_API_KEY": "provider-api-key"},
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / "bin/ods-host-agent.py"), "exec"), namespace)
    return namespace[node.name]()


class DirectoryRepairTests(unittest.TestCase):
    def test_strict_umask_directory_modes_repaired_with_secret(self):
        syscalls = DescriptorOS()
        self.assertEqual(repair(syscalls, Path("/private/remote-provider")), ["REMOTE_LLM_API_KEY"])
        self.assertEqual([stat.S_IMODE(syscalls.entries[fd].st_mode) for fd in (10, 11, 12)],
                         [0o750, 0o750, 0o640])
        self.assertEqual(set(syscalls.closed), {9, 10, 11, 12})

    def test_root_repair_keeps_directory_owners_and_only_adds_group_access(self):
        syscalls = DescriptorOS(uid=0, gid=0)
        repair(syscalls, Path("/private/remote-provider"))
        # Directories keep their owner and group; the provider-read secret
        # still moves to the dedicated container group, as before.
        self.assertEqual([(syscalls.entries[fd].st_uid, syscalls.entries[fd].st_gid) for fd in (10, 11)],
                         [(0, 0), (0, 0)])
        self.assertEqual([stat.S_IMODE(syscalls.entries[fd].st_mode) for fd in (10, 11)], [0o750, 0o750])
        self.assertEqual((syscalls.entries[12].st_gid, stat.S_IMODE(syscalls.entries[12].st_mode)),
                         (10778, 0o640))
        self.assertEqual([change[0] for change in syscalls.changes if change[1] in (10, 11)],
                         ["mode", "mode"])

    def test_existing_other_bits_are_kept(self):
        # Rootless Docker and the Dashboard API reach the route through the
        # other bits, so a 0755 folder must stay exactly 0755.
        syscalls = DescriptorOS()
        for fd in (10, 11):
            syscalls.entries[fd].st_mode = stat.S_IFDIR | 0o755
        self.assertEqual(repair(syscalls, Path("/private/remote-provider")), ["REMOTE_LLM_API_KEY"])
        self.assertEqual([stat.S_IMODE(syscalls.entries[fd].st_mode) for fd in (10, 11)], [0o755, 0o755])
        self.assertEqual([change for change in syscalls.changes if change[1] in (10, 11)], [])

    def test_strict_directory_only_gains_group_access(self):
        syscalls = DescriptorOS()
        syscalls.entries[10].st_mode = stat.S_IFDIR | 0o701
        repair(syscalls, Path("/private/remote-provider"))
        self.assertEqual(stat.S_IMODE(syscalls.entries[10].st_mode), 0o751)

    def test_foreign_directory_that_already_has_group_access_is_left_alone(self):
        syscalls = DescriptorOS()
        syscalls.entries[11].st_uid = 9999
        syscalls.entries[11].st_mode = stat.S_IFDIR | 0o750
        self.assertEqual(repair(syscalls, Path("/private/remote-provider")), ["REMOTE_LLM_API_KEY"])
        self.assertEqual(syscalls.entries[11].st_uid, 9999)
        self.assertEqual([change for change in syscalls.changes if change[1] == 11], [])

    def test_second_repair_is_idempotent(self):
        syscalls = DescriptorOS()
        repair(syscalls, Path("/private/remote-provider"))
        syscalls.changes.clear()
        self.assertEqual(repair(syscalls, Path("/private/remote-provider")), [])
        self.assertEqual(syscalls.changes, [])

    def test_foreign_directory_refused_without_permission_mutation(self):
        syscalls = DescriptorOS()
        syscalls.entries[11].st_uid = 9999
        self.assertEqual(repair(syscalls, Path("/private/remote-provider")), [])
        self.assertEqual(syscalls.changes, [])

    def test_symlink_and_non_directory_refused_without_permission_mutation(self):
        for fd in (9, 10, 11):
            for kind in (stat.S_IFLNK, stat.S_IFREG):
                syscalls = DescriptorOS()
                syscalls.entries[fd].st_mode = kind | 0o700
                self.assertEqual(repair(syscalls, Path("/private/remote-provider")), [])
                self.assertEqual(syscalls.changes, [])

    def test_failed_directory_repair_does_not_widen_secret(self):
        syscalls = DescriptorOS()
        syscalls.fail_chmod = True
        self.assertEqual(repair(syscalls, Path("/private/remote-provider")), [])
        self.assertEqual(stat.S_IMODE(syscalls.entries[12].st_mode), 0o600)

    @unittest.skipIf(os.name == "nt", "native POSIX directory modes require Linux/macOS")
    def test_native_strict_umask_directory_and_secret_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "remote-provider"
            old_umask = os.umask(0o077)
            try:
                (root / "secrets").mkdir(parents=True)
                secret = root / "secrets/provider-api-key"
                secret.write_text("fixture-provider-value\n", encoding="utf-8")
            finally:
                os.umask(old_umask)
            self.assertEqual(repair(os, root, os.getegid()), ["REMOTE_LLM_API_KEY"])
            self.assertEqual([stat.S_IMODE(path.stat().st_mode) for path in
                              (root, root / "secrets", secret)], [0o750, 0o750, 0o640])

    @unittest.skipIf(os.name == "nt", "consumer UID/group test requires POSIX sudo")
    def test_non_owner_consumer_group_can_read_provider_but_not_peer_token(self):
        if not shutil.which("sudo"):
            self.skipTest("passwordless sudo is unavailable")
        if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode:
            self.skipTest("private consumer UID fixture requires passwordless sudo")
        with tempfile.TemporaryDirectory() as directory:
            Path(directory).chmod(0o750)
            root = Path(directory) / "remote-provider"
            (root / "secrets").mkdir(parents=True, mode=0o700)
            root.chmod(0o700)
            provider = root / "secrets/provider-api-key"
            peer = root / "secrets/peer-token"
            for path in (provider, peer):
                path.write_text("private fixture\n", encoding="utf-8")
                path.chmod(0o600)
            # Match both shipped container identities (10778:10778), including
            # the supplementary installation data group from Compose. Only
            # this child drops identity; no host user/group is created.
            reader = (
                "import os, sys; os.setgroups([int(sys.argv[1])]); "
                "os.setgid(10778); os.setuid(10778); "
                "sys.stdout.buffer.write(open(sys.argv[2], 'rb').read())"
            )
            command = ["sudo", "-n", sys.executable, "-c", reader, str(os.getegid())]
            self.assertNotEqual(subprocess.run(command + [str(provider)], capture_output=True).returncode, 0)
            repair(os, root, os.getegid())
            read = subprocess.run(command + [str(provider)], capture_output=True)
            self.assertEqual(read.returncode, 0, read.stderr)
            self.assertEqual(read.stdout, b"private fixture\n")
            self.assertNotEqual(subprocess.run(command + [str(peer)], capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
