"""Exercise retained provider normalization on real temporary filesystem paths."""
import errno
import importlib.util
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cloud_permissions", ROOT / "installers/lib/cloud-config-permissions.py")
if os.name == "posix":
    helper = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(helper)


@unittest.skipUnless(os.name == "posix" and hasattr(os, "O_PATH"), "Linux filesystem semantics required")
class DescriptorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ods-cloud-permissions-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "installed"
        self.cloud = self.root / "config/litellm/cloud.yaml"
        self.cloud.parent.mkdir(parents=True)
        self.cloud.write_bytes(b"owner-provider\n")
        self.paths = [self.root, self.root / "config", self.cloud.parent, self.cloud]
        for path in self.paths:
            path.chmod(0o664 if path == self.cloud else 0o775)
        self.identity = patch.object(helper, "_require_private_group")
        self.identity.start()
        self.addCleanup(self.identity.stop)

    def modes(self):
        return [stat.S_IMODE(path.stat().st_mode) for path in self.paths]

    def test_all_legacy_paths_normalize_without_replacing_provider(self):
        before = self.cloud.stat()
        with self.cloud.open("rb") as consumer:
            self.assertEqual(helper.prepare(self.root), 4)
            self.assertEqual(consumer.read(), b"owner-provider\n")
        self.assertEqual(self.modes(), [0o755, 0o755, 0o755, 0o644])
        after = self.cloud.stat()
        for field in ("st_ino", "st_dev", "st_uid", "st_gid", "st_size", "st_mtime_ns"):
            self.assertEqual(getattr(before, field), getattr(after, field))
        self.assertEqual(helper.prepare(self.root), 0)

    def test_safe_paths_do_not_need_private_group_discovery(self):
        for path in self.paths:
            path.chmod(0o640 if path == self.cloud else 0o750)
        helper._require_private_group.side_effect = AssertionError("unexpected identity lookup")
        self.assertEqual(helper.prepare(self.root), 0)

    def test_special_read_and_execute_bits_preserved(self):
        self.root.chmod(0o2775)
        self.cloud.chmod(0o1764)
        helper.prepare(self.root)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o2755)
        self.assertEqual(stat.S_IMODE(self.cloud.stat().st_mode), 0o1744)

    def test_shared_group_rejection_changes_nothing(self):
        helper._require_private_group.side_effect = helper.Refusal("shared group")
        with self.assertRaises(helper.Refusal):
            helper.prepare(self.root)
        self.assertEqual(self.modes(), [0o775, 0o775, 0o775, 0o664])

    def test_world_writable_leaf_refuses_before_any_mutation(self):
        self.cloud.chmod(0o666)
        with patch.object(helper.os, "fchmod") as change:
            with self.assertRaises(helper.Refusal):
                helper.prepare(self.root)
            change.assert_not_called()
        self.assertEqual(self.modes(), [0o775, 0o775, 0o775, 0o666])

    def test_foreign_owner_refuses_before_any_mutation(self):
        real = os.fstat
        inode = self.cloud.stat().st_ino
        def foreign(fd):
            result = real(fd)
            if result.st_ino == inode:
                fields = list(result)
                fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return result
        with patch.object(helper.os, "fstat", side_effect=foreign), patch.object(helper.os, "fchmod") as change:
            with self.assertRaises(helper.Refusal):
                helper.prepare(self.root)
            change.assert_not_called()

    def test_hardlinked_repair_refuses_without_mutation(self):
        os.link(self.cloud, self.base / "second-link")
        with self.assertRaises(helper.Refusal):
            helper.prepare(self.root)
        self.assertEqual(self.modes(), [0o775, 0o775, 0o775, 0o664])

    def test_ancestor_symlink_refuses(self):
        alias = self.base / "alias"
        alias.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(helper.Refusal):
            helper.prepare(alias / "installed")
        self.assertEqual(self.modes(), [0o775, 0o775, 0o775, 0o664])

    def test_fifo_refuses_without_blocking(self):
        self.cloud.unlink()
        os.mkfifo(self.cloud)
        with self.assertRaises(helper.Refusal):
            helper.prepare(self.root)

    def test_noop_fchmod_does_not_report_success(self):
        with patch.object(helper.os, "fchmod"):
            with self.assertRaises(helper.Refusal):
                helper.prepare(self.root)

    def test_named_user_acl_refuses_before_mutation(self):
        entries = [(1, 6, 0xffffffff), (2, 6, os.geteuid() + 1), (4, 4, 0xffffffff), (16, 6, 0xffffffff), (32, 4, 0xffffffff)]
        acl = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *entry) for entry in entries)
        try:
            os.setxattr(self.cloud, "system.posix_acl_access", acl)
        except OSError as error:
            if error.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
                self.skipTest("filesystem has no POSIX ACL support")
            raise
        with patch.object(helper.os, "fchmod") as change:
            with self.assertRaisesRegex(helper.Refusal, "extended ACL"):
                helper.prepare(self.root)
            change.assert_not_called()
        self.assertEqual(os.getxattr(self.cloud, "system.posix_acl_access"), acl)

    def test_directory_default_acl_refuses_before_mutation(self):
        entries = [(1, 7, 0xffffffff), (2, 7, os.geteuid() + 1), (4, 5, 0xffffffff), (16, 7, 0xffffffff), (32, 5, 0xffffffff)]
        acl = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *entry) for entry in entries)
        try:
            os.setxattr(self.root, "system.posix_acl_default", acl)
        except OSError as error:
            if error.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
                self.skipTest("filesystem has no POSIX ACL support")
            raise
        with patch.object(helper.os, "fchmod") as change:
            with self.assertRaisesRegex(helper.Refusal, "extended ACL"):
                helper.prepare(self.root)
            change.assert_not_called()
        self.assertEqual(os.getxattr(self.root, "system.posix_acl_default"), acl)

    def swap_during_fchmod(self, target):
        victim = self.base / "unrelated"
        if target == self.cloud:
            victim.write_bytes(b"victim\n")
            victim.chmod(0o660)
        else:
            victim.mkdir()
            victim.chmod(0o770)
        before = victim.stat()
        original = target.with_name(target.name + "-original")
        real = os.fchmod
        def replace(fd, mode):
            target.rename(original)
            target.symlink_to(victim, target_is_directory=victim.is_dir())
            real(fd, mode)
        with patch.object(helper.os, "fchmod", side_effect=replace):
            with self.assertRaises(helper.Refusal):
                helper.prepare(self.root)
        self.assertEqual(victim.stat().st_mode, before.st_mode)
        self.assertEqual(victim.stat().st_ino, before.st_ino)

    def test_leaf_swap_never_chmods_victim(self):
        self.swap_during_fchmod(self.cloud)

    def test_parent_swap_never_chmods_victim(self):
        self.swap_during_fchmod(self.cloud.parent)

    def test_root_swap_never_chmods_victim(self):
        self.swap_during_fchmod(self.root)

    def test_missing_rsync_refuses_before_mode_changes(self):
        script = 'source "$1"; error(){ :; }; command(){ if [[ "$1" == -v && "$2" == rsync ]]; then return 1; fi; builtin command "$@"; }; ods_copy_install_source "$2" "$3" "$4"'
        result = subprocess.run(["bash", "-c", script, "fixture", str(ROOT / "installers/lib/source-copy.sh"), str(ROOT), str(self.root), str(self.base / "copy.log")], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.modes(), [0o775, 0o775, 0o775, 0o664])


@unittest.skipUnless(os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() != 0, "ordinary Linux identity required")
class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.uid, self.gid = os.geteuid(), os.getegid()
        self.records = {
            "/etc/nsswitch.conf": ["passwd: files systemd", "group: files systemd"],
            "/etc/passwd": [f"owner:x:{self.uid}:{self.gid}::/home/owner:/bin/bash"],
            "/etc/group": [f"owner:x:{self.gid}:"],
        }
        for context in (
            patch.object(helper, "_local_lines", side_effect=lambda name: self.records[name]),
            patch.object(helper.pwd, "getpwuid", return_value=SimpleNamespace(pw_name="owner", pw_uid=self.uid, pw_gid=self.gid)),
            patch.object(helper.pwd, "getpwnam", return_value=SimpleNamespace(pw_name="owner", pw_uid=self.uid, pw_gid=self.gid)),
            patch.object(helper.pwd, "getpwall", return_value=[SimpleNamespace(pw_uid=self.uid, pw_gid=self.gid)]),
            patch.object(helper.grp, "getgrgid", return_value=SimpleNamespace(gr_name="owner", gr_mem=[])),
            patch.object(helper.grp, "getgrnam", return_value=SimpleNamespace(gr_name="owner", gr_gid=self.gid, gr_mem=[])),
        ):
            context.start()
            self.addCleanup(context.stop)

    def check(self):
        helper._require_private_group(self.uid, self.gid)

    def test_owner_only_explicit_membership_is_accepted(self):
        self.records["/etc/group"] = [f"owner:x:{self.gid}:owner"]
        self.check()

    def test_additional_explicit_member_refuses(self):
        self.records["/etc/group"] = [f"owner:x:{self.gid}:owner,other"]
        with self.assertRaises(helper.Refusal):
            self.check()

    def test_additional_primary_group_user_refuses(self):
        self.records["/etc/passwd"].append(f"other:x:{self.uid + 1}:{self.gid}::/home/other:/bin/bash")
        with self.assertRaises(helper.Refusal):
            self.check()

    def test_named_nss_identity_disagreement_refuses(self):
        helper.pwd.getpwnam.return_value.pw_uid = self.uid + 1
        with self.assertRaises(helper.Refusal):
            self.check()
        helper.pwd.getpwnam.return_value.pw_uid = self.uid
        helper.grp.getgrnam.return_value.gr_gid = self.gid + 1
        with self.assertRaises(helper.Refusal):
            self.check()

    def test_nonlocal_and_ambiguous_nss_refuse(self):
        for sources in ("files sss", "compat", "files [SUCCESS=return]", "files files"):
            with self.subTest(sources=sources):
                self.records["/etc/nsswitch.conf"] = [f"passwd: {sources}", "group: files"]
                with self.assertRaises(helper.Refusal):
                    self.check()

    def test_malformed_or_duplicate_records_refuse(self):
        for database, extra in (("/etc/passwd", "bad"), ("/etc/group", "bad"), ("/etc/passwd", self.records["/etc/passwd"][0]), ("/etc/group", self.records["/etc/group"][0])):
            with self.subTest(database=database, extra=extra):
                self.records[database].append(extra)
                with self.assertRaises(helper.Refusal):
                    self.check()
                self.records[database].pop()


if __name__ == "__main__":
    unittest.main()
