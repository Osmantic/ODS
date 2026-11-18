import hashlib
import importlib.util
import os
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(HERE, "..", "lib", "model-file-sha256.py")


def load_helper():
    spec = importlib.util.spec_from_file_location("model_file_sha256", HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_helper()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, data):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def _run(self, path):
        out = mock.patch("sys.stdout")
        err = mock.patch("sys.stderr")
        with out as so, err as se:
            rc = self.mod.main(["helper", path])
        return rc, so, se

    def test_empty(self):
        p = self._write("empty", b"")
        rc, so, _ = self._run(p)
        self.assertEqual(rc, 0)
        so.write.assert_called_once_with(hashlib.sha256(b"").hexdigest() + "\n")

    def test_abc(self):
        p = self._write("abc", b"abc")
        rc, so, _ = self._run(p)
        self.assertEqual(rc, 0)
        so.write.assert_called_once_with(hashlib.sha256(b"abc").hexdigest() + "\n")

    def test_multichunk_partial_tail(self):
        data = b"x" * (self.mod.CHUNK * 2 + 123)
        p = self._write("multi", data)
        rc, so, _ = self._run(p)
        self.assertEqual(rc, 0)
        so.write.assert_called_once_with(hashlib.sha256(data).hexdigest() + "\n")

    def test_missing(self):
        rc, so, _ = self._run(os.path.join(self.tmp.name, "nope"))
        self.assertNotEqual(rc, 0)
        so.write.assert_not_called()

    def test_directory(self):
        rc, so, _ = self._run(self.tmp.name)
        self.assertNotEqual(rc, 0)
        so.write.assert_not_called()

    def test_fifo(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo unavailable")
        p = os.path.join(self.tmp.name, "fifo")
        os.mkfifo(p)
        rc, so, _ = self._run(p)
        self.assertNotEqual(rc, 0)
        so.write.assert_not_called()

    def test_valid_symlink(self):
        data = b"symlink-data"
        target = self._write("target", data)
        link = os.path.join(self.tmp.name, "link")
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        rc, so, _ = self._run(link)
        self.assertEqual(rc, 0)
        so.write.assert_called_once_with(hashlib.sha256(data).hexdigest() + "\n")

    def test_symlink_to_directory_rejected(self):
        link = os.path.join(self.tmp.name, "dirlink")
        try:
            os.symlink(self.tmp.name, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        rc, so, _ = self._run(link)
        self.assertNotEqual(rc, 0)
        so.write.assert_not_called()

    def test_advice_absent(self):
        data = b"no-advice"
        p = self._write("noadv", data)
        with mock.patch.object(self.mod.os, "posix_fadvise", None, create=True):
            rc, so, _ = self._run(p)
        self.assertEqual(rc, 0)
        so.write.assert_called_once_with(hashlib.sha256(data).hexdigest() + "\n")

    def test_advice_error_still_correct_and_warns_once(self):
        data = b"advice-error" * 1000
        p = self._write("adv", data)

        def boom(fd, off, ln, adv):
            raise OSError(5, "io")

        with mock.patch.object(self.mod.os, "posix_fadvise", boom, create=True), \
             mock.patch.object(self.mod.os, "POSIX_FADV_SEQUENTIAL", 2, create=True), \
             mock.patch.object(self.mod.os, "POSIX_FADV_DONTNEED", 4, create=True):
            rc, so, se = self._run(p)
        self.assertEqual(rc, 0)
        so.write.assert_called_once_with(hashlib.sha256(data).hexdigest() + "\n")
        self.assertEqual(''.join(c.args[0] for c in se.write.call_args_list).count('cache advice unavailable'), 1)

    def test_read_failure(self):
        p = self._write("readfail", b"abc")
        real_read = os.read
        state = {"n": 0}

        def flaky(fd, n):
            state["n"] += 1
            if state["n"] == 1:
                return real_read(fd, n)
            raise OSError(5, "io")

        with mock.patch.object(self.mod.os, "read", flaky):
            rc, so, _ = self._run(p)
        self.assertNotEqual(rc, 0)
        so.write.assert_not_called()

    def test_early_eof(self):
        data = b"y" * (self.mod.CHUNK + 10)
        p = self._write("eof", data)
        real_read = os.read
        state = {"n": 0}

        def short(fd, n):
            state["n"] += 1
            if state["n"] == 1:
                return real_read(fd, n)
            return b""

        with mock.patch.object(self.mod.os, "read", short):
            rc, so, _ = self._run(p)
        self.assertNotEqual(rc, 0)
        so.write.assert_not_called()

    def test_metadata_mutation_during_read(self):
        p = self._write("mut", b"abc")
        real_read = os.read
        initial = os.stat(p)

        def mut(fd, n):
            data = real_read(fd, n)
            os.utime(p, ns=(initial.st_atime_ns, initial.st_mtime_ns + 1_000_000_000))
            return data

        with mock.patch.object(self.mod.os, "read", mut):
            rc, so, _ = self._run(p)
        self.assertNotEqual(rc, 0)
        so.write.assert_not_called()

    def test_path_retarget(self):
        p = self._write("orig", b"abc")
        other = self._write("other", b"abc")
        real_read = os.read

        def retarget(fd, n):
            data = real_read(fd, n)
            if os.path.exists(other):
                os.replace(other, p)
            return data

        with mock.patch.object(self.mod.os, "read", retarget):
            rc, so, _ = self._run(p)
        self.assertNotEqual(rc, 0)
        so.write.assert_not_called()

    def test_open_retarget_between_stat_and_open(self):
        p = self._write("orig", b"abc")
        other = self._write("other", b"abc")
        real_open = os.open

        def retarget_open(path, flags, *a, **kw):
            os.replace(other, p)
            return real_open(path, flags, *a, **kw)

        with mock.patch.object(self.mod.os, "open", retarget_open):
            rc, so, _ = self._run(p)
        self.assertNotEqual(rc, 0)
        so.write.assert_not_called()

    def test_close_failure_yields_no_stdout(self):
        p = self._write("closefail", b"abc")
        real_close = os.close

        def bad_close(fd):
            real_close(fd)
            raise OSError(5, "io")

        with mock.patch.object(self.mod.os, "close", bad_close):
            rc, so, _ = self._run(p)
        self.assertNotEqual(rc, 0)
        so.write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
