"""Focused contract tests for the explicit, pinned ComfyUI model download."""
import hashlib
import importlib.util
import io
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1] / "bin" / "comfy_checkpoint.py"
spec = importlib.util.spec_from_file_location("ods_comfy_checkpoint", MODULE)
checkpoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checkpoint)


class FakeResponse:
    def __init__(self, body, code=200, headers=None, first_read=None, release_read=None):
        self.stream = io.BytesIO(body)
        self.code = code
        self.headers = headers or {}
        self.first_read = first_read
        self.release_read = release_read

    def getcode(self):
        return self.code

    def read(self, count):
        if self.first_read is not None:
            self.first_read.set()
            self.first_read = None
        if self.release_read is not None:
            self.release_read.wait(5)
            self.release_read = None
        return self.stream.read(count)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return self.response


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "data" / "comfyui" / "models" / "checkpoints"
        self.target.mkdir(parents=True)
        self.payload = b"ODS verified Comfy checkpoint fixture\n"
        self.hash = hashlib.sha256(self.payload).hexdigest()
        size_patch = patch.object(checkpoint, "SIZE_BYTES", len(self.payload))
        hash_patch = patch.object(checkpoint, "SHA256", self.hash)
        size_patch.start()
        hash_patch.start()
        self.addCleanup(size_patch.stop)
        self.addCleanup(hash_patch.stop)

    def manager(self, response=None):
        opener = FakeOpener(response or FakeResponse(self.payload))
        return checkpoint.CheckpointManager(self.root, "nvidia", _opener=opener), opener

    def finish(self, manager):
        manager._thread.join(5)
        self.assertFalse(manager._thread.is_alive())
        return manager.status()

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_idle_status_does_not_download_or_create_stage(self):
        manager, opener = self.manager()
        self.assertEqual(manager.status()["state"], "idle")
        self.assertFalse((self.root / ".ods-comfy-checkpoint").exists())
        self.assertEqual(opener.requests, [])

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_existing_model_is_not_reported_verified_without_hashing(self):
        (self.target / checkpoint.FILENAME).write_bytes(self.payload)
        manager, opener = self.manager()
        self.assertEqual(manager.status()["state"], "existing_unverified")
        self.assertEqual(opener.requests, [])
        self.assertFalse((self.root / ".ods-comfy-checkpoint").exists())

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_invalid_existing_model_is_never_replaced(self):
        final = self.target / checkpoint.FILENAME
        final.write_bytes(b"X" * len(self.payload))
        manager, opener = self.manager()
        manager.start(checkpoint.MODEL_ID, len(self.payload))
        self.assertEqual(self.finish(manager)["error"], "existing_checkpoint_invalid")
        self.assertEqual(final.read_bytes(), b"X" * len(self.payload))
        self.assertEqual(opener.requests, [])

    def test_confirmation_is_required_before_filesystem_mutation(self):
        manager, opener = self.manager()
        with self.assertRaises(checkpoint.CheckpointError) as failure:
            manager.start(checkpoint.MODEL_ID, len(self.payload) + 1)
        self.assertEqual(failure.exception.code, "checkpoint_confirmation_required")
        self.assertFalse((self.root / ".ods-comfy-checkpoint").exists())
        self.assertEqual(opener.requests, [])

    def test_unsupported_host_rejects_valid_confirmation_without_mutation(self):
        manager, opener = self.manager()
        with patch.object(checkpoint, "fcntl", None):
            self.assertEqual(manager.status()["state"], "unsupported")
            with self.assertRaises(checkpoint.CheckpointError) as failure:
                manager.start(checkpoint.MODEL_ID, len(self.payload))
        self.assertEqual(failure.exception.code, "unsupported_checkpoint_host")
        self.assertFalse((self.root / ".ods-comfy-checkpoint").exists())
        self.assertEqual(opener.requests, [])

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_download_verifies_and_promotes_without_touching_chat_status(self):
        manager, opener = self.manager()
        manager.start(checkpoint.MODEL_ID, len(self.payload))
        status = self.finish(manager)
        self.assertEqual(status["state"], "done")
        self.assertEqual((self.target / checkpoint.FILENAME).read_bytes(), self.payload)
        self.assertEqual(len(opener.requests), 1)
        self.assertFalse((self.root / "data" / "model-download-status.json").exists())

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_same_size_replacement_revokes_verified_status(self):
        manager, _ = self.manager()
        manager.start(checkpoint.MODEL_ID, len(self.payload))
        self.assertEqual(self.finish(manager)["state"], "done")
        final = self.target / checkpoint.FILENAME
        replacement = self.target / "replacement.tmp"
        replacement.write_bytes(b"X" * len(self.payload))
        os.replace(replacement, final)
        status = manager.status()
        self.assertEqual(status["state"], "error")
        self.assertEqual(status["error"], "checkpoint_missing_or_changed")

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_library_reenable_chmod_reverifies_saved_checkpoint_without_download(self):
        manager, opener = self.manager()
        manager.start(checkpoint.MODEL_ID, len(self.payload))
        self.assertEqual(self.finish(manager)["state"], "done")
        final = self.target / checkpoint.FILENAME
        before = final.stat()
        time.sleep(0.01)
        os.chmod(final, before.st_mode ^ stat.S_IWGRP)
        after = final.stat()
        self.assertEqual((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                         (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns))
        self.assertNotEqual(after.st_ctime_ns, before.st_ctime_ns)

        self.assertEqual(manager.status()["state"], "verifying")
        self.assertEqual(self.finish(manager)["state"], "done")
        self.assertEqual(final.read_bytes(), self.payload)
        self.assertEqual(len(opener.requests), 1)  # initial transfer only

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_same_inode_tamper_with_restored_mtime_fails_reverification(self):
        manager, opener = self.manager()
        manager.start(checkpoint.MODEL_ID, len(self.payload))
        self.assertEqual(self.finish(manager)["state"], "done")
        final = self.target / checkpoint.FILENAME
        before = final.stat()
        time.sleep(0.01)
        final.write_bytes(b"X" * len(self.payload))
        os.utime(final, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = final.stat()
        self.assertEqual((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                         (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns))
        self.assertNotEqual(after.st_ctime_ns, before.st_ctime_ns)

        self.assertEqual(manager.status()["state"], "verifying")
        result = self.finish(manager)
        self.assertEqual(result["state"], "error")
        self.assertEqual(result["error"], "checkpoint_missing_or_changed")
        self.assertEqual(len(opener.requests), 1)
        failed_worker = manager._thread
        self.assertEqual(manager.status()["state"], "error")
        self.assertIs(manager._thread, failed_worker)  # no repeated 7 GB hash loop

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_prior_agent_false_error_recovers_only_after_full_hash(self):
        manager, opener = self.manager()
        manager.start(checkpoint.MODEL_ID, len(self.payload))
        self.assertEqual(self.finish(manager)["state"], "done")
        final = self.target / checkpoint.FILENAME
        stage = self.root / ".ods-comfy-checkpoint"
        manager._write(stage, "error", 0, "checkpoint_missing_or_changed")

        self.assertEqual(manager.status()["state"], "verifying")
        self.assertEqual(self.finish(manager)["state"], "done")
        self.assertEqual(final.read_bytes(), self.payload)
        self.assertEqual(len(opener.requests), 1)

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_reverification_is_single_flight_and_has_no_download_cancel(self):
        manager, _ = self.manager()
        manager.start(checkpoint.MODEL_ID, len(self.payload))
        self.assertEqual(self.finish(manager)["state"], "done")
        final = self.target / checkpoint.FILENAME
        os.chmod(final, final.stat().st_mode ^ stat.S_IWGRP)
        entered, release = threading.Event(), threading.Event()
        original_verify = manager._verify

        def held_verify(*args, **kwargs):
            if not kwargs.get("report_progress", True):
                entered.set()
                release.wait(5)
            return original_verify(*args, **kwargs)

        with patch.object(manager, "_verify", side_effect=held_verify):
            self.assertEqual(manager.status()["state"], "verifying")
            self.assertTrue(entered.wait(5))
            worker = manager._thread
            self.assertEqual(manager.status()["state"], "verifying")
            self.assertIs(manager._thread, worker)
            with self.assertRaises(checkpoint.CheckpointError) as failure:
                manager.cancel()
            self.assertEqual(failure.exception.code, "checkpoint_not_running")
            release.set()
            self.assertEqual(self.finish(manager)["state"], "done")

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_replacement_during_metadata_reverification_never_promotes(self):
        manager, _ = self.manager()
        manager.start(checkpoint.MODEL_ID, len(self.payload))
        self.assertEqual(self.finish(manager)["state"], "done")
        final = self.target / checkpoint.FILENAME
        os.chmod(final, final.stat().st_mode ^ stat.S_IWGRP)
        original_verify = manager._verify

        def swap_after_hash(*args, **kwargs):
            identity = original_verify(*args, **kwargs)
            replacement = self.target / "replacement.tmp"
            replacement.write_bytes(b"X" * len(self.payload))
            os.replace(replacement, final)
            return identity

        with patch.object(manager, "_verify", side_effect=swap_after_hash):
            self.assertEqual(manager.status()["state"], "verifying")
            result = self.finish(manager)
        self.assertEqual(result["state"], "error")
        self.assertEqual(result["error"], "checkpoint_missing_or_changed")

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_replacement_during_publication_is_never_reported_verified(self):
        manager, _ = self.manager()
        real_link = os.link

        def replace_after_link(*args, **kwargs):
            real_link(*args, **kwargs)
            foreign = self.target / "foreign.tmp"
            foreign.write_bytes(b"X" * len(self.payload))
            os.replace(foreign, self.target / checkpoint.FILENAME)

        with patch.object(checkpoint.os, "link", side_effect=replace_after_link):
            manager.start(checkpoint.MODEL_ID, len(self.payload))
            status = self.finish(manager)
        self.assertEqual(status["state"], "error")
        self.assertEqual(status["error"], "checkpoint_hash_mismatch")
        self.assertNotEqual(status.get("verified_file"), checkpoint._file_identity(
            (self.target / checkpoint.FILENAME).stat()))

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_wrong_hash_never_promotes(self):
        manager, _ = self.manager(FakeResponse(b"X" * len(self.payload)))
        manager.start(checkpoint.MODEL_ID, len(self.payload))
        status = self.finish(manager)
        self.assertEqual(status["error"], "checkpoint_hash_mismatch")
        self.assertFalse((self.target / checkpoint.FILENAME).exists())
        self.assertFalse((self.root / ".ods-comfy-checkpoint" / (checkpoint.FILENAME + ".part")).exists())

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_short_response_keeps_verified_resume_offset(self):
        manager, _ = self.manager(FakeResponse(self.payload[:5]))
        manager.start(checkpoint.MODEL_ID, len(self.payload))
        status = self.finish(manager)
        self.assertEqual(status["error"], "checkpoint_incomplete")
        self.assertTrue(status["resumable"])
        self.assertEqual((self.root / ".ods-comfy-checkpoint" /
                          (checkpoint.FILENAME + ".part")).read_bytes(), self.payload[:5])

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_resume_uses_range_and_validates_complete_file(self):
        manager, opener = self.manager(FakeResponse(
            self.payload[4:], 206,
            {"Content-Range": "bytes 4-{}/{}".format(len(self.payload) - 1, len(self.payload))},
        ))
        stage = manager._stage()
        (stage / (checkpoint.FILENAME + ".part")).write_bytes(self.payload[:4])
        checkpoint._atomic_json(stage / (checkpoint.FILENAME + ".part.json"), {
            "revision": checkpoint.REVISION, "size_bytes": len(self.payload), "sha256": self.hash,
        })
        manager.start(checkpoint.MODEL_ID, len(self.payload))
        self.assertEqual(self.finish(manager)["state"], "done")
        self.assertEqual(opener.requests[0].get_header("Range"), "bytes=4-")
        self.assertEqual((self.target / checkpoint.FILENAME).read_bytes(), self.payload)

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_server_ignoring_range_restarts_cleanly(self):
        manager, opener = self.manager(FakeResponse(self.payload, 200))
        stage = manager._stage()
        (stage / (checkpoint.FILENAME + ".part")).write_bytes(b"bad")
        checkpoint._atomic_json(stage / (checkpoint.FILENAME + ".part.json"), {
            "revision": checkpoint.REVISION, "size_bytes": len(self.payload), "sha256": self.hash,
        })
        manager.start(checkpoint.MODEL_ID, len(self.payload))
        self.assertEqual(self.finish(manager)["state"], "done")
        self.assertEqual(opener.requests[0].get_header("Range"), "bytes=3-")
        self.assertEqual((self.target / checkpoint.FILENAME).read_bytes(), self.payload)

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_invalid_partial_range_never_promotes(self):
        manager, _ = self.manager(FakeResponse(
            self.payload[4:], 206,
            {"Content-Range": "bytes 4-5/{}".format(len(self.payload))},
        ))
        stage = manager._stage()
        part = stage / (checkpoint.FILENAME + ".part")
        part.write_bytes(self.payload[:4])
        checkpoint._atomic_json(stage / (checkpoint.FILENAME + ".part.json"), {
            "revision": checkpoint.REVISION, "size_bytes": len(self.payload), "sha256": self.hash,
        })
        manager.start(checkpoint.MODEL_ID, len(self.payload))
        self.assertEqual(self.finish(manager)["error"], "checkpoint_range_mismatch")
        self.assertFalse((self.target / checkpoint.FILENAME).exists())

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_cancel_preserves_partial_and_releases_lock(self):
        started, release = threading.Event(), threading.Event()
        manager, _ = self.manager(FakeResponse(
            self.payload[4:], 206,
            {"Content-Range": "bytes 4-{}/{}".format(len(self.payload) - 1, len(self.payload))},
            first_read=started, release_read=release,
        ))
        stage = manager._stage()
        part = stage / (checkpoint.FILENAME + ".part")
        part.write_bytes(self.payload[:4])
        checkpoint._atomic_json(stage / (checkpoint.FILENAME + ".part.json"), {
            "revision": checkpoint.REVISION, "size_bytes": len(self.payload), "sha256": self.hash,
        })
        manager.start(checkpoint.MODEL_ID, len(self.payload))
        self.assertTrue(started.wait(5))
        second, _ = self.manager()
        with self.assertRaises(checkpoint.CheckpointError) as failure:
            second.start(checkpoint.MODEL_ID, len(self.payload))
        self.assertEqual(failure.exception.code, "checkpoint_busy")
        self.assertEqual(manager.cancel()["state"], "cancelling")
        release.set()
        self.assertEqual(self.finish(manager)["state"], "cancelled")
        self.assertEqual(part.read_bytes(), self.payload[:4])
        second.start(checkpoint.MODEL_ID, len(self.payload))
        self.assertEqual(self.finish(second)["state"], "done")

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_symlinked_checkpoint_directory_is_rejected(self):
        self.target.rmdir()
        outside = self.root / "outside"
        outside.mkdir()
        self.target.symlink_to(outside, target_is_directory=True)
        manager, _ = self.manager()
        with self.assertRaises(checkpoint.CheckpointError) as failure:
            manager.start(checkpoint.MODEL_ID, len(self.payload))
        self.assertEqual(failure.exception.code, "unsafe_checkpoint_directory")

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_directory_hop_swapped_after_precheck_cannot_redirect_open(self):
        manager, _ = self.manager()
        manager._target()
        models = self.target.parent
        original = self.root / "original-models"
        models.rename(original)
        outside = self.root / "outside"
        outside.mkdir()
        models.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            manager._target_fd()

    def test_redirect_to_non_publisher_host_is_rejected(self):
        redirect = checkpoint._PinnedRedirect()
        with self.assertRaises(checkpoint.CheckpointError) as failure:
            redirect.redirect_request(None, None, 302, "", {}, "http://127.0.0.1/private")
        self.assertEqual(failure.exception.code, "redirect_rejected")

    @unittest.skipUnless(os.name == "posix", "requires POSIX no-follow and flock")
    def test_active_status_from_dead_host_agent_is_interrupted(self):
        manager, _ = self.manager()
        stage = manager._stage()
        checkpoint._atomic_json(stage / "status.json", {"state": "downloading", "bytes_done": 4})
        self.assertEqual(manager.status()["state"], "interrupted")


if __name__ == "__main__":
    unittest.main()
