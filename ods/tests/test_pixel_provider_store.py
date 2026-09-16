"""Real filesystem and concurrency tests, confined to private temp fixtures."""

from __future__ import annotations

import copy
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
from pixel_provider import store
from pixel_provider.config import default_config


def _writer(directory, queue):
    try:
        store.ProviderStore(directory).save(default_config(), expected_revision=0)
        queue.put("ok")
    except store.StoreError as exc:
        queue.put(exc.code)


@unittest.skipUnless(os.name == "posix", "POSIX custody/locking adapter")
class Persistence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / "state"
        self.directory.mkdir(mode=0o700)
        self.storage = store.ProviderStore(self.directory)
        self.config = self.directory / store.CONFIG_NAME

    def seed(self):
        return self.storage.save(default_config(), expected_revision=0)

    def raw(self, payload):
        self.config.write_bytes(payload)
        self.config.chmod(0o600)

    def test_roundtrip_and_caller_independence(self):
        self.assertEqual(self.storage.load(), default_config())
        self.assertFalse(self.config.exists())
        value = default_config()
        original = copy.deepcopy(value)
        saved = self.storage.save(value, expected_revision=0)
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(self.storage.load(), saved)
        self.assertEqual(value, original)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)

    def test_validator_cannot_erase_increment(self):
        def invalid_validator(value):
            value["revision"] = 0
            return value
        storage = store.ProviderStore(self.directory, validator=invalid_validator)
        with self.assertRaises(store.StoreError) as exc:
            storage.save(default_config(), expected_revision=0)
        self.assertEqual(exc.exception.code, "invalid-config")
        self.assertFalse(self.config.exists())

    def test_stale_and_invalid_preserve_exact_bytes(self):
        saved = self.seed()
        before = self.config.read_bytes()
        for revision, value, code in (
            (0, default_config(), "stale-revision"),
            (1, default_config(), "stale-revision"),
            (True, saved, "invalid-request"),
            (-1, saved, "invalid-request"),
            (1, {**saved, "secret": "never-echo-this"}, "invalid-config"),
        ):
            with self.subTest(code=code, revision=revision):
                with self.assertRaises(store.StoreError) as exc:
                    self.storage.save(value, expected_revision=revision)
                self.assertEqual(exc.exception.code, code)
                self.assertNotIn("never-echo-this", str(exc.exception))
                self.assertEqual(self.config.read_bytes(), before)

    def test_corrupt_never_defaults(self):
        for raw in (b'{"x":1,"x":2}', b'NaN', b'1e999', b'"\xff"',
                    b'[' * 5000 + b']' * 5000, b'x' * (store.MAX_BYTES + 1), b'{}'):
            with self.subTest(raw=raw[:8]):
                self.raw(raw)
                with self.assertRaises(store.StoreError):
                    self.storage.load()
                with self.assertRaises(store.StoreError):
                    self.storage.save(default_config(), expected_revision=0)
                self.assertEqual(self.config.read_bytes(), raw)

    def test_unsafe_files_and_locks(self):
        for name in (store.CONFIG_NAME, store.LOCK_NAME):
            for kind in ("mode", "symlink", "hardlink", "directory", "fifo"):
                with self.subTest(name=name, kind=kind), tempfile.TemporaryDirectory() as scratch:
                    directory = Path(scratch)
                    path = directory / name
                    target = directory / "target"
                    target.write_bytes(b"{}")
                    target.chmod(0o600)
                    if kind == "mode":
                        path.write_bytes(b"{}")
                        path.chmod(0o644)
                    elif kind == "symlink":
                        path.symlink_to(target)
                    elif kind == "hardlink":
                        os.link(target, path)
                    elif kind == "directory":
                        path.mkdir(mode=0o700)
                    else:
                        os.mkfifo(path, 0o600)
                    with self.assertRaises(store.StoreError):
                        store.ProviderStore(directory).load()

    def test_unsafe_directory(self):
        self.directory.chmod(0o755)
        with self.assertRaises(store.StoreError):
            self.storage.load()
        self.directory.chmod(0o700)
        link = self.root / "link"
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(store.StoreError):
            store.ProviderStore(link).load()

    def test_partial_write_completes(self):
        original_write = os.write
        with patch.object(store.os, "write", side_effect=lambda fd, raw: original_write(fd, raw[:7])):
            saved = self.seed()
        self.assertEqual(self.storage.load(), saved)

    def test_pre_rename_failure_preserves(self):
        saved = self.seed()
        before = self.config.read_bytes()
        with patch.object(store.os, "fsync", side_effect=OSError("fixture-secret")):
            with self.assertRaises(store.StoreError) as exc:
                self.storage.save(saved, expected_revision=1)
        self.assertEqual(exc.exception.code, "write-failed")
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(list(self.directory.glob(".provider-*.tmp")), [])

    def test_post_rename_failure_is_explicitly_ambiguous(self):
        original_fsync = os.fsync
        calls = []
        def fail_second(fd):
            calls.append(fd)
            if len(calls) == 2:
                raise OSError("fixture-secret")
            return original_fsync(fd)
        with patch.object(store.os, "fsync", side_effect=fail_second):
            with self.assertRaises(store.StoreError) as exc:
                self.seed()
        self.assertEqual(exc.exception.code, "write-durability-unknown")
        self.assertEqual(self.storage.load()["revision"], 1)

    def test_directory_swap_rejected(self):
        original_fsync = os.fsync
        moved = self.root / "moved"
        target = self.root / "target"
        target.mkdir(mode=0o700)
        def swap(fd):
            original_fsync(fd)
            self.directory.rename(moved)
            self.directory.symlink_to(target, target_is_directory=True)
        with patch.object(store.os, "fsync", side_effect=swap):
            with self.assertRaises(store.StoreError) as exc:
                self.seed()
        self.assertEqual(exc.exception.code, "directory-replaced")
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(list(moved.glob(".provider-*.tmp")), [])

    def test_threads_exactly_one_wins(self):
        barrier = threading.Barrier(2)
        outcomes = []
        def writer():
            barrier.wait(timeout=2)
            try:
                store.ProviderStore(self.directory).save(default_config(), expected_revision=0)
                outcomes.append("ok")
            except store.StoreError as exc:
                outcomes.append(exc.code)
        threads = [threading.Thread(target=writer) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=6)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, ["ok", "stale-revision"])

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "fork unavailable")
    def test_processes_exactly_one_wins(self):
        ctx = multiprocessing.get_context("fork")
        queue = ctx.Queue()
        children = [ctx.Process(target=_writer, args=(self.directory, queue)) for _ in range(2)]
        try:
            for child in children:
                child.start()
            for child in children:
                child.join(timeout=6)
                self.assertFalse(child.is_alive())
                self.assertEqual(child.exitcode, 0)
            self.assertCountEqual([queue.get(timeout=2), queue.get(timeout=2)], ["ok", "stale-revision"])
        finally:
            for child in children:
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=2)
            queue.close()

    def test_lock_timeout_and_stable_inode(self):
        self.storage.load()
        lock = self.directory / store.LOCK_NAME
        original_inode = lock.stat().st_ino
        fd = os.open(lock, os.O_RDWR)
        try:
            store.fcntl.flock(fd, store.fcntl.LOCK_EX | store.fcntl.LOCK_NB)
            with patch.object(store, "LOCK_TIMEOUT", 0.05):
                with self.assertRaises(store.StoreError) as exc:
                    self.storage.load()
            self.assertEqual(exc.exception.code, "lock-timeout")
        finally:
            os.close(fd)
        self.assertEqual(lock.stat().st_ino, original_inode)
        self.seed()
        self.assertEqual(lock.stat().st_ino, original_inode)


if __name__ == "__main__":
    unittest.main()
