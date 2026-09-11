#!/usr/bin/env python3
"""Adversarial unit tests for ODS Pixel's exact-download promoter."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import socket
import stat
import tempfile
import threading
import time
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).parents[1] / "host" / "artifact_promoter.py"
SPEC = importlib.util.spec_from_file_location("ods_pixel_artifact_promoter", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
promoter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(promoter)


class ArtifactPromoterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = pathlib.Path(self.temporary.name)
        self.results = root / "results"
        self.artifacts = root / "artifacts"
        self.workspace = root / "workspace"
        for path in (self.results, self.artifacts, self.workspace):
            path.mkdir(mode=0o700)
        self.uid = os.getuid()
        self.gid = os.getgid()
        self.job_id = "ops-1788130169655-22b40ab50141"
        self.filename = "reference.html"
        self.relative_path = "web/reference.html"
        self.source_url = "https://example.com/"
        self.payload = b"<!doctype html>\nexact bytes\n"
        self.digest = hashlib.sha256(self.payload).hexdigest()
        job = self.artifacts / self.job_id
        job.mkdir(mode=0o700)
        artifact = job / self.filename
        artifact.write_bytes(self.payload)
        os.chmod(artifact, 0o600)
        result = {
            "schemaVersion": 2,
            "jobId": self.job_id,
            "status": "succeeded",
            "steps": [
                {
                    "target": "broker",
                    "action": "download.stage",
                    "exitCode": 0,
                    "artifact": {
                        "path": str(artifact),
                        "filename": self.filename,
                        "bytes": len(self.payload),
                        "sha256": self.digest,
                        "source": self.source_url,
                        "redirects": [],
                        "executable": False,
                    },
                }
            ],
        }
        result_path = self.results / f"{self.job_id}.json"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        os.chmod(result_path, 0o600)

    def request(self, **changes: object) -> dict[str, object]:
        value: dict[str, object] = {
            "schemaVersion": 1,
            "action": "promote",
            "jobId": self.job_id,
            "filename": self.filename,
            "relativePath": self.relative_path,
            "sha256": self.digest,
            "sourceUrl": self.source_url,
        }
        value.update(changes)
        return promoter.parse_request(json.dumps(value).encode())

    def promote(
        self,
        request: dict[str, object] | None = None,
        cancel_requested=None,
    ) -> dict[str, object]:
        return promoter.promote(
            request or self.request(),
            results_root=self.results,
            artifacts_root=self.artifacts,
            workspace=self.workspace,
            broker_uid=self.uid,
            owner_uid=self.uid,
            owner_gid=self.gid,
            cancel_requested=cancel_requested,
        )

    def test_request_grammar_is_exact_and_health_is_separate(self) -> None:
        self.assertEqual(self.request()["relativePath"], self.relative_path)
        self.assertTrue(
            promoter.is_health_request(b'{"schemaVersion":1,"action":"health"}')
        )
        self.assertFalse(
            promoter.is_health_request(
                b'{"schemaVersion":1,"action":"health","path":"/etc/shadow"}'
            )
        )
        with self.assertRaises(promoter.PromotionError):
            self.request(command="id")
        with self.assertRaises(promoter.PromotionError):
            self.request(sourceUrl="https://example.com/file\nignored")
        for relative_path in (
            "/etc/passwd",
            "../escape",
            "web/../../escape",
            "web\\escape",
            "web/not-the-filename",
        ):
            with self.subTest(relative_path=relative_path):
                with self.assertRaises(promoter.PromotionError):
                    self.request(relativePath=relative_path)

    def test_mode_is_sealed_before_owner_custody_transfer(self) -> None:
        calls = []
        with (
            mock.patch.object(
                promoter.os,
                "fchmod",
                side_effect=lambda descriptor, mode: calls.append(
                    ("fchmod", descriptor, mode)
                ),
            ),
            mock.patch.object(
                promoter.os,
                "fchown",
                side_effect=lambda descriptor, uid, gid: calls.append(
                    ("fchown", descriptor, uid, gid)
                ),
            ),
        ):
            promoter._secure_fd_for_owner(9, 0o600, 1000, 1000)
        self.assertEqual(
            calls,
            [("fchmod", 9, 0o600), ("fchown", 9, 1000, 1000)],
        )

        calls.clear()
        path = pathlib.Path("/run/example.sock")
        with (
            mock.patch.object(
                promoter.os,
                "chmod",
                side_effect=lambda candidate, mode: calls.append(
                    ("chmod", candidate, mode)
                ),
            ),
            mock.patch.object(
                promoter.os,
                "chown",
                side_effect=lambda candidate, uid, gid: calls.append(
                    ("chown", candidate, uid, gid)
                ),
            ),
        ):
            promoter._secure_path_for_owner(path, 0o600, 1000, 1000)
        self.assertEqual(
            calls,
            [("chmod", path, 0o600), ("chown", path, 1000, 1000)],
        )

    def test_promotes_exact_bytes_create_only_and_non_executable(self) -> None:
        result = self.promote()
        target = self.workspace / self.relative_path
        self.assertEqual(target.read_bytes(), self.payload)
        info = target.lstat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(info.st_uid, self.uid)
        self.assertEqual(result["bytes"], len(self.payload))
        self.assertEqual(result["sha256"], self.digest)
        self.assertEqual(result["source"], self.source_url)
        self.assertEqual(result["requestedSource"], self.source_url)
        self.assertFalse(result["executable"])
        self.assertFalse(result["overwritten"])

        with self.assertRaises(promoter.PromotionError):
            self.promote()
        self.assertEqual(target.read_bytes(), self.payload)

    def test_rejects_result_source_digest_and_quarantine_tampering(self) -> None:
        result_path = self.results / f"{self.job_id}.json"
        original = json.loads(result_path.read_text(encoding="utf-8"))
        for field, value in (
            ("source", "https://different.example/"),
            ("sha256", "0" * 64),
            ("executable", True),
        ):
            changed = json.loads(json.dumps(original))
            changed["steps"][0]["artifact"][field] = value
            result_path.write_text(json.dumps(changed), encoding="utf-8")
            os.chmod(result_path, 0o600)
            with self.subTest(field=field):
                with self.assertRaises(promoter.PromotionError):
                    self.promote()
        result_path.write_text(json.dumps(original), encoding="utf-8")
        os.chmod(result_path, 0o600)
        (self.artifacts / self.job_id / self.filename).write_bytes(b"tampered")
        os.chmod(self.artifacts / self.job_id / self.filename, 0o600)
        with self.assertRaises(promoter.PromotionError):
            self.promote()
        self.assertFalse((self.workspace / self.relative_path).exists())

    def test_rejects_symlinked_workspace_parent(self) -> None:
        outside = pathlib.Path(self.temporary.name) / "outside"
        outside.mkdir(mode=0o700)
        (self.workspace / "web").symlink_to(outside, target_is_directory=True)
        with self.assertRaises((promoter.PromotionError, OSError)):
            self.promote()
        self.assertFalse((outside / self.filename).exists())

    def test_disconnected_request_is_rolled_back_before_publication(self) -> None:
        with self.assertRaises(promoter.PromotionError):
            self.promote(cancel_requested=lambda: True)
        self.assertFalse((self.workspace / self.relative_path).exists())
        self.assertEqual(list(self.workspace.iterdir()), [self.workspace / "web"])
        self.assertEqual(list((self.workspace / "web").iterdir()), [])

    def test_disconnect_after_create_only_link_removes_only_new_target(self) -> None:
        checks = 0

        def disconnect_after_link() -> bool:
            nonlocal checks
            checks += 1
            return checks == 4

        with self.assertRaises(promoter.PromotionError):
            self.promote(cancel_requested=disconnect_after_link)
        self.assertFalse((self.workspace / self.relative_path).exists())
        self.assertEqual(list((self.workspace / "web").iterdir()), [])

    def test_socket_disconnect_cancels_before_workspace_publication(self) -> None:
        server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        worker = threading.Thread(
            target=promoter._serve_connection,
            args=(server,),
            kwargs={
                "owner_uid": self.uid,
                "owner_gid": self.gid,
                "broker_uid": self.uid,
                "results_root": self.results,
                "artifacts_root": self.artifacts,
                "workspace": self.workspace,
            },
        )
        worker.start()
        request = {
            "schemaVersion": 1,
            "action": "promote",
            "jobId": self.job_id,
            "filename": self.filename,
            "relativePath": self.relative_path,
            "sha256": self.digest,
            "sourceUrl": self.source_url,
        }
        client.sendall((json.dumps(request) + "\n").encode())
        client.close()
        worker.join(timeout=5)
        server.close()
        self.assertFalse(worker.is_alive())
        self.assertFalse((self.workspace / self.relative_path).exists())

    def test_connected_socket_receives_success_without_false_disconnect(self) -> None:
        server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        worker = threading.Thread(
            target=promoter._serve_connection,
            args=(server,),
            kwargs={
                "owner_uid": self.uid,
                "owner_gid": self.gid,
                "broker_uid": self.uid,
                "results_root": self.results,
                "artifacts_root": self.artifacts,
                "workspace": self.workspace,
            },
        )
        try:
            worker.start()
            client.settimeout(2)
            wire_request = {
                "schemaVersion": 1,
                "action": "promote",
                **self.request(),
            }
            client.sendall((json.dumps(wire_request) + "\n").encode())
            response = bytearray()
            while not response.endswith(b"\n"):
                response.extend(client.recv(4096))
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(json.loads(response)["status"], "succeeded")
            self.assertEqual(
                (self.workspace / self.relative_path).read_bytes(), self.payload
            )
        finally:
            client.close()
            server.close()

    def test_connected_idle_socket_is_not_reported_disconnected(self) -> None:
        server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # The live service applies a bounded read timeout before promotion.
            # A connected requester has no more bytes to send while it waits for
            # the result, so the liveness check itself must remain non-blocking.
            server.settimeout(10)
            started = time.monotonic()
            disconnected = promoter._connection_closed(server)
            elapsed = time.monotonic() - started
            self.assertFalse(disconnected)
            self.assertLess(elapsed, 0.5)
        finally:
            client.close()
            server.close()


if __name__ == "__main__":
    unittest.main()
