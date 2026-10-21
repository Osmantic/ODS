import importlib.util
import io
import json
import os
import socket
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "artifact_preview", Path(__file__).parents[1] / "host/workspace_preview.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / "workspace"
        self.previews = self.root / "snapshots"
        self.workspace.mkdir(mode=0o700)
        self.previews.mkdir(mode=0o700)
        self.project = self.workspace / "project"
        self.project.mkdir(mode=0o700)

    def write(self, rel, data, mode=0o600):
        p = self.workspace / rel
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        p.write_bytes(data)
        p.chmod(mode)
        return p

    def publish(self, source="project", out="project/out.zip"):
        return m.publish_artifact_archive(
            self.workspace, self.previews, out, source, os.getuid()
        )

    def read_zip(self, receipt):
        dest = self.previews / receipt["siteId"]
        return zipfile.ZipFile(io.BytesIO((dest / "out.zip").read_bytes()))

    def test_deterministic_unicode_crlf_binary_exact(self):
        self.write("project/a.txt", b"line1\r\nline2\r\n")
        self.write("project/unicode.txt", "caf\u00e9 \u4e2d\u6587\n".encode())
        self.write("project/bin.dat", bytes(range(256)))
        r1 = self.publish()
        r2 = self.publish()
        self.assertEqual(r1["sha256"], r2["sha256"])
        self.assertEqual(r1["siteId"], r2["siteId"])
        z = self.read_zip(r1)
        self.assertEqual(z.read("project/a.txt"), b"line1\r\nline2\r\n")
        self.assertEqual(z.read("project/unicode.txt"), "caf\u00e9 \u4e2d\u6587\n".encode())
        self.assertEqual(z.read("project/bin.dat"), bytes(range(256)))
        for info in z.infolist():
            self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
        self.assertEqual(r1["archive"]["fileCount"], 3)

    def test_default_caches_and_git_excluded(self):
        self.write("project/keep.txt", b"k")
        self.write("project/__pycache__/x.pyc", b"c")
        self.write("project/.git/HEAD", b"ref")
        self.write("project/node_modules/x.js", b"n")
        self.write("project/.pytest_cache/x", b"p")
        self.write("project/skip.pyc", b"c")
        self.write("project/.DS_Store", b"d")
        r = self.publish()
        z = self.read_zip(r)
        self.assertEqual(z.namelist(), ["project/keep.txt"])
        self.assertEqual(r["archive"]["omittedCacheEntries"], 6)

    def test_dist_build_output_retained(self):
        self.write("project/dist/a.js", b"d")
        self.write("project/build/b.js", b"b")
        self.write("project/output/c.js", b"o")
        r = self.publish()
        names = self.read_zip(r).namelist()
        self.assertIn("project/dist/a.js", names)
        self.assertIn("project/build/b.js", names)
        self.assertIn("project/output/c.js", names)

    def test_known_hidden_gitignore_preserved_unknown_rejected(self):
        self.write("project/.gitignore", b"*.log\n")
        self.write("project/.gitattributes", b"* text=auto\n")
        r = self.publish()
        names = self.read_zip(r).namelist()
        self.assertIn("project/.gitignore", names)
        self.assertIn("project/.gitattributes", names)
        self.write("project/.env", b"SECRET=1")
        with self.assertRaises(m.PreviewError):
            self.publish()
        (self.workspace / "project/.env").unlink()
        self.write("project/.private", b"x")
        with self.assertRaises(m.PreviewError):
            self.publish()

    def test_workspace_unchanged_and_preexisting_output_untouched(self):
        self.write("project/a.txt", b"a")
        existing = self.write("project/out.zip", b"original workspace output")
        before = {}
        for p in self.workspace.rglob("*"):
            if p.is_file():
                before[str(p)] = (p.read_bytes(), p.stat().st_mtime_ns)
        pre = self.previews / ("site-" + "0" * 24)
        pre.mkdir(mode=0o700)
        (pre / "out.zip").write_bytes(b"preexisting")
        (pre / "out.zip").chmod(0o400)
        r = self.publish()
        self.assertEqual(existing.read_bytes(), b"original workspace output")
        self.assertNotEqual(r["siteId"], "site-" + "0" * 24)
        self.assertEqual((pre / "out.zip").read_bytes(), b"preexisting")
        for p in self.workspace.rglob("*"):
            if p.is_file():
                self.assertEqual(before[str(p)], (p.read_bytes(), p.stat().st_mtime_ns))

    def test_single_source_file_byte_exact(self):
        self.write("project/one.py", b"print('hi')\r\n")
        r = self.publish(source="project/one.py")
        z = self.read_zip(r)
        self.assertEqual(z.namelist(), ["one.py"])
        self.assertEqual(z.read("one.py"), b"print('hi')\r\n")

    def test_existing_direct_artifact_remains_unchanged(self):
        self.write("project/a.txt", b"direct bytes")
        r = m.publish_artifact(self.workspace, self.previews, "project/a.txt", os.getuid())
        self.assertNotIn("archive", r)
        self.assertEqual(r["boundary"], m.ARTIFACT_BOUNDARY)
        self.assertEqual((self.previews / r["siteId"] / "a.txt").read_bytes(), b"direct bytes")

    def test_publication_readback_via_http_and_socketpair(self):
        self.write("project/a.txt", b"hello")
        with m.PreviewHTTPServer(("127.0.0.1", 0), self.previews) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                client, peer = socket.socketpair()
                client.settimeout(5)
                self.addCleanup(client.close)
                self.addCleanup(peer.close)
                thread = threading.Thread(
                    target=m._serve_connection,
                    args=(peer,),
                    kwargs={
                        "workspace": self.workspace,
                        "previews": self.previews,
                        "owner_uid": os.getuid(),
                        "port": server.server_port,
                    },
                )
                thread.start()
                client.sendall(
                    b'{"schemaVersion":1,"action":"publish-artifact",'
                    b'"relativePath":"project/out.zip","sourcePath":"project"}\n'
                )
                client.shutdown(socket.SHUT_WR)
                chunks = bytearray()
                while b"\n" not in chunks and len(chunks) <= 8192:
                    chunk = client.recv(8192 - len(chunks))
                    if not chunk: break
                    chunks.extend(chunk)
                result = json.loads(chunks)
                thread.join(5)
                self.assertFalse(thread.is_alive())
                client.close()
                peer.close()
                self.assertEqual(result["status"], "succeeded")
                self.assertTrue(result["readbackVerified"])
                self.assertEqual(result["httpStatus"], 200)
                self.assertEqual(result["archive"]["sourcePath"], "project")
            finally:
                server.shutdown()
                worker.join()

    def test_symlink_hardlink_wrongowner_modes_fifo(self):
        self.write("project/a.txt", b"a")
        (self.project / "link.txt").symlink_to(self.project / "a.txt")
        with self.assertRaises(m.PreviewError):
            self.publish()
        (self.project / "link.txt").unlink()
        os.link(self.project / "a.txt", self.project / "hard.txt")
        with self.assertRaises(m.PreviewError):
            self.publish()
        (self.project / "hard.txt").unlink()
        self.write("project/w.txt", b"w", mode=0o666)
        with self.assertRaises(m.PreviewError):
            self.publish()
        (self.project / "w.txt").chmod(0o600)
        os.mkfifo(self.project / "fifo")
        with self.assertRaises(m.PreviewError):
            self.publish()
        (self.project / "fifo").unlink()
        with self.assertRaises(m.PreviewError):
            m.publish_artifact_archive(
                self.workspace, self.previews, "project/out.zip", "project",
                os.getuid() + 1,
            )

    def test_boundaries_files_size_total_output_depth_casefold(self):
        for i in range(m.ARCHIVE_MAX_FILES + 1):
            self.write(f"project/f{i:03d}.txt", b"x")
        with self.assertRaises(m.PreviewError):
            self.publish()
        for p in self.project.iterdir():
            p.unlink()
        self.write("project/big.bin", b"x" * (m.ARCHIVE_MAX_FILE_BYTES + 1))
        with self.assertRaises(m.PreviewError):
            self.publish()
        (self.project / "big.bin").unlink()
        self.write("project/a.txt", b"a")
        deep = "project/" + "/".join(["d"] * (m.ARCHIVE_MAX_DEPTH + 1)) + "/x.txt"
        self.write(deep, b"x")
        with self.assertRaises(m.PreviewError):
            self.publish()

    def test_source_changes_after_read(self):
        self.write("project/a.txt", b"a" * 1000)
        original = m._archive_read_file
        changed = {"n": 0}

        def read_then_change(parent_fd, name, owner_uid):
            data, info = original(parent_fd, name, owner_uid)
            if changed["n"] == 0:
                changed["n"] = 1
                (self.project / "a.txt").write_bytes(b"b" * 1000)
            return data, info

        with patch.object(m, "_archive_read_file", side_effect=read_then_change):
            with self.assertRaises(m.PreviewError):
                self.publish()
        self.assertEqual(list(self.previews.iterdir()), [])

    def test_tree_changes_final_scan(self):
        self.write("project/a.txt", b"a")
        original = m._archive_manifest_walk
        added = {"n": 0}

        def walk(fd, prefix, depth, state, manifest):
            if added["n"] == 0:
                added["n"] = 1
                (self.project / "late.txt").write_bytes(b"late")
            return original(fd, prefix, depth, state, manifest)

        with patch.object(m, "_archive_manifest_walk", side_effect=walk):
            with self.assertRaises(m.PreviewError):
                self.publish()

    def test_fd_leaks_on_error(self):
        if not os.path.isdir("/proc/self/fd"):
            self.skipTest("Linux descriptor inventory required")
        self.write("project/a.txt", b"a")
        self.write("project/b.txt", b"b")
        os.link(self.project / "b.txt", self.project / "c.txt")
        before = len(os.listdir("/proc/self/fd")) if os.path.isdir("/proc/self/fd") else None
        for _ in range(5):
            with self.assertRaises(m.PreviewError):
                self.publish()
        if before is not None:
            after = len(os.listdir("/proc/self/fd"))
            self.assertEqual(after, before)

    def test_selected_cache_private_sources_and_output_validation(self):
        for name, data in [("skip.pyc", b"cache"), ("secret.txt", b"private"),
                           ("key.txt", b"api_key=private-value")]:
            self.write("project/" + name, data)
            with self.subTest(name=name), self.assertRaises(m.PreviewError):
                self.publish(source="project/" + name)
        for params in [{"relativePath":"x.txt","sourcePath":"project"},
                       {"relativePath":"x.zip","sourcePath":"../outside"},
                       {"relativePath":"x.zip","sourcePath":"project","extra":1}]:
            with self.subTest(params=params), self.assertRaises(m.PreviewError):
                m.parse_request(json.dumps({"schemaVersion":1,"action":"publish-artifact",**params}).encode())
        self.assertEqual(list(self.previews.iterdir()), [])

    def test_mutation_during_descriptor_read(self):
        source = self.write("project/a.txt", b"a" * 100000)
        original = os.read
        changed = False
        def read(fd, count):
            nonlocal changed
            data = original(fd, count)
            if not changed:
                changed = True
                source.write_bytes(b"b" * 100000)
            return data
        with patch.object(m.os,"read",side_effect=read), self.assertRaises(m.PreviewError):
            self.publish()
        self.assertEqual(list(self.previews.iterdir()), [])

    def test_total_and_compressed_output_bounds(self):
        for i in range(5): self.write(f"project/f{i}.dat", b"x" * m.ARCHIVE_MAX_FILE_BYTES)
        with self.assertRaises(m.PreviewError): self.publish()
        for p in self.project.iterdir(): p.unlink()
        self.write("project/a.dat", os.urandom(m.ARCHIVE_MAX_FILE_BYTES))
        self.write("project/b.dat", os.urandom(4096))
        with self.assertRaises(m.PreviewError): self.publish()
        self.assertEqual(list(self.previews.iterdir()), [])

    def test_empty_tree(self):
        with self.assertRaises(m.PreviewError): self.publish()

    def test_case_colliding_files(self):
        self.write("project/a.txt", b"a")
        if (self.project / "A.txt").exists():
            self.skipTest("filesystem cannot represent case-distinct file entries")
        self.write("project/A.txt", b"A")
        with self.assertRaises(m.PreviewError): self.publish()

    def test_case_colliding_directories(self):
        self.write("project/A/one.txt",b"one")
        if (self.project / "a").exists():
            self.skipTest("filesystem cannot represent case-distinct directory entries")
        self.write("project/a/two.txt",b"two")
        with self.assertRaises(m.PreviewError): self.publish()

    def test_git_worktree_metadata_is_not_read_or_removed(self):
        meta = self.write("project/.git", b"gitdir: private metadata path")
        self.write("project/main.py", b"print(1)\n")
        r = self.publish()
        self.assertEqual(self.read_zip(r).namelist(), ["project/main.py"])
        self.assertEqual(r["archive"]["omittedCacheEntries"], 1)
        self.assertEqual(meta.read_bytes(), b"gitdir: private metadata path")


if __name__ == "__main__":
    unittest.main()
