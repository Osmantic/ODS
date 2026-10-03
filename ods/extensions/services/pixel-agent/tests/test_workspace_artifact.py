import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "artifact_preview", Path(__file__).parents[1] / "host/workspace_preview.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.previews = self.root / "snapshots"
        self.workspace.mkdir(mode=0o700)
        self.previews.mkdir(mode=0o700)
        (self.workspace / "project").mkdir(mode=0o700)

    def file(self, name="report.md", data=b"precise bytes\n"):
        p = self.workspace / "project" / name
        p.write_bytes(data)
        p.chmod(0o600)
        return p

    def publish(self, path="project/report.md"):
        return m.publish_artifact(self.workspace, self.previews, path, os.getuid())

    def test_document_only_formats_exact_bytes_empty_and_no_sibling_publication(self):
        for name, data in [
            ("report.md", b"# Report"),
            ("report.PDF", b"%PDF binary\x00"),
            ("package.zip", b"PK\x00"),
            ("a.rar", b"Rar!"),
            ("empty.txt", b""),
            ("a.json", b"{not executed}"),
            ("a.tsv", b"a\tb"),
            ("a.csv", b"a,b"),
            ("a.docx", b"PKdoc"),
            ("a.xlsx", b"PKsheet"),
            ("a.pptx", b"PKslides"),
        ]:
            with self.subTest(name=name):
                source = self.file(name, data)
                receipt = self.publish("project/" + name)
                self.assertEqual(receipt["kind"], "ods-pixel-workspace-artifact")
                self.assertEqual(
                    receipt["file"],
                    {
                        "path": name,
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    },
                )
                dest = self.previews / receipt["siteId"]
                self.assertEqual(list(x.name for x in dest.iterdir()), [name])
                source.write_bytes(b"changed")
                self.assertEqual((dest / name).read_bytes(), data)
        self.assertFalse((self.workspace / "project/index.html").exists())

    def test_same_snapshot_reuse_and_tampering_rejected(self):
        self.file()
        one = self.publish()
        self.assertEqual(one, self.publish())
        (self.previews / one["siteId"] / "report.md").chmod(0o600)
        with self.assertRaises(m.PreviewError):
            self.publish()

    def test_bounds_types_links_ownership(self):
        self.file(data=b"x" * m.MAX_FILE_BYTES)
        self.assertEqual(self.publish()["file"]["bytes"], m.MAX_FILE_BYTES)
        self.file(data=b"x" * (m.MAX_FILE_BYTES + 1))
        with self.assertRaises(m.PreviewError):
            self.publish()
        self.file()
        os.link(
            self.workspace / "project/report.md", self.workspace / "project/hard.md"
        )
        with self.assertRaises(m.PreviewError):
            self.publish("project/hard.md")
        self.file("other.txt")
        (self.workspace / "project/link.txt").symlink_to(
            self.workspace / "project/other.txt"
        )
        with self.assertRaises(OSError):
            self.publish("project/link.txt")
        (self.workspace / "linked").symlink_to(
            self.workspace / "project", target_is_directory=True
        )
        with self.assertRaises(OSError):
            self.publish("linked/other.txt")
        (self.workspace / "project/other.txt").chmod(0o666)
        with self.assertRaises(m.PreviewError):
            self.publish("project/other.txt")
        with self.assertRaises(m.PreviewError):
            m.publish_artifact(
                self.workspace, self.previews, "project/other.txt", os.getuid() + 1
            )

    def test_invalid_request_paths_fields_and_formats(self):
        for path in [
            "/etc/passwd",
            "../secret.md",
            "project/../secret.md",
            "project/%2e%2e/x.md",
            "project/a\\b.md",
            "project/.env",
            "project/a.exe",
            "project/a.docm",
            "project/a.xlsm",
            "project/a.pptm",
            "project/a.html",
            "project/a.md?x",
        ]:
            with self.subTest(path=path), self.assertRaises(m.PreviewError):
                m.parse_request(
                    json.dumps(
                        {
                            "schemaVersion": 1,
                            "action": "publish-artifact",
                            "relativePath": path,
                        }
                    ).encode()
                )
        for extra in [
            {"url": "http://example.org"},
            {"relativeDirectory": "project"},
            {"schemaVersion": True},
        ]:
            with self.assertRaises(m.PreviewError):
                m.parse_request(
                    json.dumps(
                        {
                            "schemaVersion": 1,
                            "action": "publish-artifact",
                            "relativePath": "project/a.md",
                            **extra,
                        }
                    ).encode()
                )

    def test_changed_source_during_read_is_rejected(self):
        source = self.file(data=b"a" * 100000)
        original = os.read
        changed = False

        def read(fd, count):
            nonlocal changed
            data = original(fd, count)
            if not changed:
                changed = True
                source.write_bytes(b"b" * 100000)
            return data

        with (
            patch.object(m.os, "read", side_effect=read),
            self.assertRaises(m.PreviewError),
        ):
            self.publish()
        self.assertEqual(list(self.previews.iterdir()), [])

    def test_ancestor_swap_cannot_redirect_descriptor_to_outside_file(self):
        self.file(data=b"inside")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "report.md").write_bytes(b"secret")
        original = os.open
        swapped = False

        def opened(path, flags, *args, **kwargs):
            nonlocal swapped
            fd = original(path, flags, *args, **kwargs)
            if path == "project" and not swapped:
                swapped = True
                (self.workspace / "project").rename(self.workspace / "original")
                (self.workspace / "project").symlink_to(
                    outside, target_is_directory=True
                )
            return fd

        with patch.object(m.os, "open", side_effect=opened):
            receipt = self.publish()
        self.assertEqual(
            (self.previews / receipt["siteId"] / "report.md").read_bytes(), b"inside"
        )

    def test_http_readback_and_private_socket_receipt(self):
        self.file(data=b"report bytes")
        with m.PreviewHTTPServer(("127.0.0.1", 0), self.previews) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                for owner in [os.getuid(), os.getuid() + 1]:
                    client, peer = socket.socketpair()
                    thread = threading.Thread(
                        target=m._serve_connection,
                        args=(peer,),
                        kwargs={
                            "workspace": self.workspace,
                            "previews": self.previews,
                            "owner_uid": owner,
                            "port": server.server_port,
                        },
                    )
                    thread.start()
                    client.sendall(
                        b'{"schemaVersion":1,"action":"publish-artifact","relativePath":"project/report.md"}\n'
                    )
                    client.shutdown(socket.SHUT_WR)
                    result = json.loads(client.recv(8192))
                    thread.join()
                    client.close()
                    peer.close()
                    self.assertEqual(
                        result["status"],
                        "succeeded" if owner == os.getuid() else "failed",
                    )
                    if owner == os.getuid():
                        self.assertTrue(result["readbackVerified"])
                        self.assertEqual(result["httpStatus"], 200)
            finally:
                server.shutdown()
                worker.join()

    def test_website_still_requires_index(self):
        self.file()
        with self.assertRaisesRegex(m.PreviewError, "requires index.html"):
            m.publish_snapshot(self.workspace, self.previews, "project", os.getuid())


if __name__ == "__main__":
    unittest.main()
