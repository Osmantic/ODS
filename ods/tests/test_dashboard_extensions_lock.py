import os
import sys
import stat
import tempfile
import unittest
import unittest.mock
import importlib.util
import fcntl
import subprocess
import json

_HELPER_PATH = os.environ.get(
    "DASHBOARD_LOCK_HELPER",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "installers", "lib", "dashboard-lock.py"),
)
_spec = importlib.util.spec_from_file_location("dashboard_lock", _HELPER_PATH)
helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helper)


class TestHelper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.lock = os.path.join(self.dir, ".extensions-lock")

    def tearDown(self):
        self.tmp.cleanup()

    def _create_file(self, mode=0o660, content=b"x"):
        fd = os.open(self.lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            os.write(fd, content)
        finally:
            os.close(fd)

    def _can_chown_gid(self, gid):
        if os.geteuid() == 0:
            return True
        try:
            return gid == os.getegid() or gid in os.getgroups()
        except OSError:
            return False

    def test_new_file_umask_restored(self):
        old = os.umask(0o077)
        try:
            res = helper.prepare(self.dir)
            self.assertEqual(os.umask(0o077), 0o077)
        finally:
            os.umask(old)
        self.assertTrue(res["created"])
        st = os.stat(self.lock)
        self.assertEqual(res["inode"], st.st_ino)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o660)
        self.assertEqual(res["mode"], 0o660)

    def test_existing_inode_preserved(self):
        self._create_file()
        ino = os.stat(self.lock).st_ino
        res = helper.prepare(self.dir)
        self.assertFalse(res["created"])
        self.assertEqual(res["inode"], ino)
        self.assertEqual(os.stat(self.lock).st_ino, ino)

    def test_symlink_rejected_victim_untouched(self):
        victim = os.path.join(self.dir, "victim")
        with open(victim, "w") as f:
            f.write("safe")
        os.symlink(victim, self.lock)
        with self.assertRaises(OSError):
            helper.prepare(self.dir)
        with open(victim) as f:
            self.assertEqual(f.read(), "safe")
        self.assertTrue(os.path.islink(self.lock))

    def test_fifo_rejected(self):
        os.mkfifo(self.lock)
        with self.assertRaises(OSError):
            helper.prepare(self.dir)

    def test_multilink_rejected(self):
        self._create_file()
        os.link(self.lock, os.path.join(self.dir, "other"))
        with self.assertRaises(OSError):
            helper.prepare(self.dir)

    def test_prepare_existing_000_retains_metadata(self):
        self._create_file(mode=0o000)
        st_before = os.stat(self.lock)
        res = helper.prepare(self.dir)
        st_after = os.stat(self.lock)
        self.assertFalse(res["created"])
        self.assertEqual(st_before.st_uid, st_after.st_uid)
        self.assertEqual(st_before.st_gid, st_after.st_gid)
        self.assertEqual(stat.S_IMODE(st_before.st_mode), stat.S_IMODE(st_after.st_mode))
        self.assertEqual(st_before.st_ino, st_after.st_ino)

    def test_repair_symlink_rejected(self):
        victim = os.path.join(self.dir, "victim")
        with open(victim, "w") as f:
            f.write("safe")
        os.symlink(victim, self.lock)
        with self.assertRaises(OSError):
            helper.repair(self.dir)
        with open(victim) as f:
            self.assertEqual(f.read(), "safe")
        self.assertTrue(os.path.islink(self.lock))

    def test_repair_fifo_rejected(self):
        os.mkfifo(self.lock)
        with self.assertRaises(OSError):
            helper.repair(self.dir)

    def test_repair_multilink_rejected(self):
        self._create_file()
        os.link(self.lock, os.path.join(self.dir, "other"))
        with self.assertRaises(OSError):
            helper.repair(self.dir)

    def test_repair_mode_group(self):
        self._create_file(mode=0o600)
        st = os.stat(self.lock)
        if st.st_uid != os.getuid():
            self.skipTest("not owner")
        if not self._can_chown_gid(1000):
            self.skipTest("cannot chown to gid 1000")
        res = helper.repair(self.dir)
        st2 = os.stat(self.lock)
        self.assertEqual(res["inode"], st.st_ino)
        self.assertEqual(st2.st_ino, st.st_ino)
        self.assertEqual(stat.S_IMODE(st2.st_mode), 0o660)
        self.assertEqual(st2.st_gid, 1000)

    def test_repair_race_fchmod_replaces_entry(self):
        self._create_file(mode=0o600, content=b"original")
        orig_ino = os.stat(self.lock).st_ino
        victim = os.path.join(self.dir, "victim")
        with open(victim, "w") as f:
            f.write("victim")
        os.chmod(victim, 0o600)
        victim_st_before = os.stat(victim)
        real_fchmod = os.fchmod
        real_fchown = os.fchown
        state = {"replaced": False}

        def fake_fchmod(fd, mode):
            if not state["replaced"]:
                state["replaced"] = True
                os.rename(self.lock, self.lock + ".original")
                os.symlink(victim, self.lock)
            return real_fchmod(fd, mode)

        def fake_fchown(fd, uid, gid):
            return real_fchown(fd, uid, gid)

        with unittest.mock.patch.object(helper, "_DASHBOARD_GID", os.getgid()):
            with unittest.mock.patch("os.fchmod", side_effect=fake_fchmod):
                with unittest.mock.patch("os.fchown", side_effect=fake_fchown):
                    with self.assertRaises(OSError):
                        helper.repair(self.dir)
        self.assertTrue(state["replaced"])
        self.assertEqual(os.stat(self.lock + ".original").st_ino, orig_ino)
        victim_st_after = os.stat(victim)
        self.assertEqual(victim_st_before.st_uid, victim_st_after.st_uid)
        self.assertEqual(victim_st_before.st_gid, victim_st_after.st_gid)
        self.assertEqual(stat.S_IMODE(victim_st_before.st_mode), stat.S_IMODE(victim_st_after.st_mode))
        with open(victim) as f:
            self.assertEqual(f.read(), "victim")
        self.assertTrue(os.path.islink(self.lock))
        self.assertNotEqual(os.stat(self.lock, follow_symlinks=False).st_ino, orig_ino)

    def test_repair_race_replace_after_open_before_mutation(self):
        self._create_file(mode=0o600, content=b"original")
        orig_ino = os.stat(self.lock).st_ino
        victim = os.path.join(self.dir, "victim")
        with open(victim, "w") as f:
            f.write("victim")
        os.chmod(victim, 0o600)
        victim_st_before = os.stat(victim)
        real_open = os.open
        state = {"replaced": False}

        def fake_open(path, flags, mode=0o777, *, dir_fd=None):
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
            if path == ".extensions-lock" and dir_fd is not None and not state["replaced"]:
                state["replaced"] = True
                os.rename(self.lock, self.lock + ".original")
                os.symlink(victim, self.lock)
            return fd

        with unittest.mock.patch.object(helper, "_DASHBOARD_GID", os.getgid()):
            with unittest.mock.patch("os.open", side_effect=fake_open):
                with self.assertRaises(OSError):
                    helper.repair(self.dir)
        self.assertTrue(state["replaced"])
        self.assertEqual(os.stat(self.lock + ".original").st_ino, orig_ino)
        victim_st_after = os.stat(victim)
        self.assertEqual(victim_st_before.st_uid, victim_st_after.st_uid)
        self.assertEqual(victim_st_before.st_gid, victim_st_after.st_gid)
        self.assertEqual(stat.S_IMODE(victim_st_before.st_mode), stat.S_IMODE(victim_st_after.st_mode))
        with open(victim) as f:
            self.assertEqual(f.read(), "victim")
        self.assertTrue(os.path.islink(self.lock))
        self.assertNotEqual(os.stat(self.lock, follow_symlinks=False).st_ino, orig_ino)

    @unittest.skipUnless(os.geteuid() == 0, "numeric ownership fixture requires root")
    def test_repair_race_fchown_replaces_entry(self):
        self._create_file(mode=0o600, content=b"original")
        os.chown(self.lock, 1001, 2001)
        orig_ino = os.stat(self.lock).st_ino
        victim = os.path.join(self.dir, "victim")
        with open(victim, "w") as f:
            f.write("victim")
        os.chmod(victim, 0o600)
        victim_st_before = os.stat(victim)
        real_fchown = os.fchown
        state = {"replaced": False}

        def fake_fchown(fd, uid, gid):
            if not state["replaced"]:
                state["replaced"] = True
                os.rename(self.lock, self.lock + ".original")
                os.symlink(victim, self.lock)
            return real_fchown(fd, uid, gid)

        with unittest.mock.patch.object(helper, "_DASHBOARD_GID", os.getgid()):
            with unittest.mock.patch("os.fchown", side_effect=fake_fchown):
                with self.assertRaises(OSError):
                    helper.repair(self.dir)
        self.assertTrue(state["replaced"])
        self.assertEqual(os.stat(self.lock + ".original").st_ino, orig_ino)
        victim_st_after = os.stat(victim)
        self.assertEqual(victim_st_before.st_uid, victim_st_after.st_uid)
        self.assertEqual(victim_st_before.st_gid, victim_st_after.st_gid)
        self.assertEqual(stat.S_IMODE(victim_st_before.st_mode), stat.S_IMODE(victim_st_after.st_mode))
        with open(victim) as f:
            self.assertEqual(f.read(), "victim")
        self.assertTrue(os.path.islink(self.lock))
        self.assertNotEqual(os.stat(self.lock, follow_symlinks=False).st_ino, orig_ino)

    def test_repair_closed_descriptors(self):
        self._create_file(mode=0o600)
        real_close = os.close
        closed = []

        def tracking_close(fd):
            closed.append(fd)
            return real_close(fd)

        with unittest.mock.patch.object(helper, "_DASHBOARD_GID", os.getgid()):
            with unittest.mock.patch("os.close", side_effect=tracking_close):
                helper.repair(self.dir)
        self.assertGreaterEqual(len(closed), 2)
        for fd in closed:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_repair_holds_flock_same_inode(self):
        self._create_file(mode=0o600)
        ino = os.stat(self.lock).st_ino
        lock_fd = os.open(self.lock, os.O_RDONLY | os.O_CLOEXEC)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with unittest.mock.patch.object(helper, "_DASHBOARD_GID", os.getgid()):
                res = helper.repair(self.dir)
            self.assertEqual(res["inode"], ino)
            self.assertEqual(os.stat(self.lock).st_ino, ino)
            other = os.open(self.lock, os.O_RDONLY | os.O_CLOEXEC)
            try:
                with self.assertRaises(OSError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(other)
        finally:
            os.close(lock_fd)

    @unittest.skipUnless(os.geteuid() == 0, "numeric ownership fixture requires root")
    def test_install_owner_and_api_share_repaired_inode(self):
        # Load the exact helper before dropping IDs; CI checkouts can live in
        # private runner homes. Only this disposable directory is changed.
        with open(_HELPER_PATH) as stream:
            source = stream.read()
        os.chown(self.dir, 501, 2001)
        os.chmod(self.dir, 0o750)
        script = ("import os,json\nnamespace={'__name__':'fixture'}\n"
                  + "exec(" + repr(source) + ", namespace)\n"
                  + "os.setgroups([]);os.setgid(2001);os.setuid(501)\n"
                  + "print(json.dumps(namespace['prepare'](" + repr(self.dir) + ")))\n")
        created = json.loads(subprocess.check_output([sys.executable, "-c", script], text=True))
        self.assertEqual(created["uid"], 501)
        # Model a retained lock created by the API, including unreadable mode.
        os.chown(self.lock, 1000, 1000)
        os.chmod(self.lock, 0o000)
        os.chown(self.dir, 501, 1000)
        os.chmod(self.dir, 0o770)
        repaired = helper.repair(self.dir)
        self.assertEqual((repaired["uid"], repaired["gid"], repaired["mode"]), (501, 1000, 0o660))
        self.assertEqual(repaired["inode"], created["inode"])
        for uid, gid in [(501, 2001), (1000, 1000)]:
            probe = ("import os,fcntl\nos.setgroups([]);os.setgid(" + str(gid)
                     + ");os.setuid(" + str(uid) + ")\n"
                     + "fd=os.open(" + repr(self.lock) + ", os.O_RDWR)\n"
                     + "fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);os.close(fd)\n")
            subprocess.run([sys.executable, "-c", probe], check=True, timeout=5)


if __name__ == "__main__":
    unittest.main()
