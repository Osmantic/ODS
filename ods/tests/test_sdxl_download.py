"""Exercise checkpoint publication with inert bytes, never downloading weights."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "scripts/download-sdxl-model.py"
SPEC = importlib.util.spec_from_file_location("sdxl_download", SOURCE)
sdxl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sdxl)
PAYLOAD = b"inert checkpoint fixture\n"


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ods checkpoint test ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "models with spaces"
        self.directory.mkdir()
        self.destination = self.directory / sdxl.FILENAME
        for name, value in (("SIZE", len(PAYLOAD)),
                            ("SHA256", hashlib.sha256(PAYLOAD).hexdigest())):
            patcher = mock.patch.object(sdxl, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def transfer(self, command, **kwargs):
        candidate = Path(command[command.index("--output") + 1])
        candidate.write_bytes(PAYLOAD)
        self.assertEqual(candidate.parent.parent, self.directory)
        self.assertFalse(self.destination.exists())
        return subprocess.CompletedProcess(command, 0)

    def assert_clean(self):
        self.assertEqual(list(self.directory.glob(".ods-sdxl-*")), [])

    def test_verified_download_publishes_atomically_with_bounded_https(self):
        with mock.patch.object(sdxl.subprocess, "run", side_effect=self.transfer) as run:
            self.assertEqual(sdxl.download(self.directory), self.destination)
        self.assertEqual(self.destination.read_bytes(), PAYLOAD)
        command = run.call_args.args[0]
        self.assertEqual(command[-1], sdxl.URL)
        self.assertNotIn("/resolve/main/", command[-1])
        for option, expected in (("--proto", "=https"), ("--proto-redir", "=https"),
                                 ("--max-filesize", str(len(PAYLOAD))),
                                 ("--connect-timeout", "30"), ("--max-time", "3600"),
                                 ("--retry-max-time", "3600")):
            self.assertEqual(command[command.index(option) + 1], expected)
        self.assertEqual(run.call_args.kwargs, {"check": True, "timeout": 3700})
        self.assert_clean()

    def test_verified_cache_does_not_use_network(self):
        self.destination.write_bytes(PAYLOAD)
        with mock.patch.object(sdxl.subprocess, "run") as run:
            self.assertEqual(sdxl.download(self.directory), self.destination)
        run.assert_not_called()

    def test_invalid_cache_is_preserved_without_network(self):
        for payload in (b"", b"partial", b"X" * len(PAYLOAD)):
            with self.subTest(payload=payload):
                self.destination.write_bytes(payload)
                with mock.patch.object(sdxl.subprocess, "run") as run:
                    with self.assertRaisesRegex(RuntimeError, "Existing checkpoint"):
                        sdxl.download(self.directory)
                run.assert_not_called()
                self.assertEqual(self.destination.read_bytes(), payload)

    def test_invalid_download_never_reaches_checkpoint_path(self):
        for payload in (b"", b"partial", b"X" * len(PAYLOAD)):
            with self.subTest(payload=payload):
                def transfer(command, **kwargs):
                    Path(command[command.index("--output") + 1]).write_bytes(payload)
                with mock.patch.object(sdxl.subprocess, "run", side_effect=transfer):
                    with self.assertRaisesRegex(RuntimeError, "size or SHA-256"):
                        sdxl.download(self.directory)
                self.assertFalse(self.destination.exists())
                self.assert_clean()

    def test_transfer_failures_clean_partial_file(self):
        for error in (subprocess.CalledProcessError(22, "curl"),
                      subprocess.TimeoutExpired("curl", 3700), FileNotFoundError("curl")):
            with self.subTest(error=type(error).__name__):
                def transfer(command, **kwargs):
                    Path(command[command.index("--output") + 1]).write_bytes(b"partial")
                    raise error
                with mock.patch.object(sdxl.subprocess, "run", side_effect=transfer):
                    with self.assertRaises(type(error)):
                        sdxl.download(self.directory)
                self.assertFalse(self.destination.exists())
                self.assert_clean()

    def test_concurrent_publication_never_overwrites_other_owner_file(self):
        real_link = os.link
        for payload in (PAYLOAD, b"another download"):
            with self.subTest(valid=payload == PAYLOAD):
                def race(source, destination):
                    self.destination.write_bytes(payload)
                    real_link(source, destination)
                with mock.patch.object(sdxl.subprocess, "run", side_effect=self.transfer):
                    with mock.patch.object(sdxl.os, "link", side_effect=race):
                        if payload == PAYLOAD:
                            sdxl.download(self.directory)
                        else:
                            with self.assertRaisesRegex(RuntimeError, "Concurrent checkpoint"):
                                sdxl.download(self.directory)
                self.assertEqual(self.destination.read_bytes(), payload)
                self.destination.unlink()
                self.assert_clean()

    def test_filesystem_without_hardlinks_fails_without_publishing(self):
        with mock.patch.object(sdxl.subprocess, "run", side_effect=self.transfer):
            with mock.patch.object(sdxl.os, "link", side_effect=OSError("unsupported")):
                with self.assertRaises(OSError):
                    sdxl.download(self.directory)
        self.assertFalse(self.destination.exists())
        self.assert_clean()

    def test_existing_directory_is_not_a_checkpoint(self):
        self.destination.mkdir()
        with self.assertRaisesRegex(RuntimeError, "Existing checkpoint"):
            sdxl.download(self.directory)

    def test_symlink_is_rejected_without_changing_target(self):
        target = self.root / "owner-file"
        target.write_bytes(PAYLOAD)
        try:
            self.destination.symlink_to(target)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                self.skipTest("Windows account cannot create symbolic links")
            raise
        with mock.patch.object(sdxl.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "Existing checkpoint"):
                sdxl.download(self.directory)
        run.assert_not_called()
        self.assertEqual(target.read_bytes(), PAYLOAD)

    def test_cli_failure_returns_nonzero_and_success_is_explicit(self):
        with mock.patch("sys.argv", [str(SOURCE), str(self.directory)]):
            with mock.patch.object(sdxl, "download", side_effect=RuntimeError("bad digest")):
                with mock.patch("builtins.print") as output:
                    self.assertEqual(sdxl.main(), 1)
                self.assertIn("[SDXL] ERROR", output.call_args.args[0])
            with mock.patch.object(sdxl, "download", return_value=self.destination):
                with mock.patch("builtins.print") as output:
                    self.assertEqual(sdxl.main(), 0)
                self.assertIn(sdxl.SHA256, output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
