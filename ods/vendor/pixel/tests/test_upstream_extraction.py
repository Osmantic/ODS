import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "extract-upstream-package.py"
SPEC = importlib.util.spec_from_file_location("pixel_upstream_extract", MODULE_PATH)
upstream = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(upstream)


class UpstreamExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def archive(self, members):
        path = self.root / f"archive-{len(list(self.root.glob('*.tgz')))}.tgz"
        with tarfile.open(path, "w:gz") as bundle:
            for name, kind, payload in members:
                info = tarfile.TarInfo(name)
                if kind == "file":
                    data = payload.encode()
                    info.size = len(data)
                    bundle.addfile(info, io.BytesIO(data))
                elif kind == "symlink":
                    info.type = tarfile.SYMTYPE
                    info.linkname = payload
                    bundle.addfile(info)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return path, digest

    def test_valid_archive_is_private_deterministic_and_tamper_evident(self):
        archive, digest = self.archive([
            ("package/package.json", "file", json.dumps({"name": "openclaw", "version": "1.0.0"})),
            ("package/bin/cli.js", "file", "export const command = '--help';"),
        ])
        destination = self.root / "extracted"
        first = upstream.extract(archive, destination, digest)
        second = upstream.extract(archive, destination, digest)
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(first["treeSha256"], second["treeSha256"])
        if __import__("os").name != "nt":
            self.assertEqual(stat.S_IMODE((destination / "package.json").stat().st_mode) & 0o077, 0)
        (destination / "bin" / "cli.js").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(upstream.ExtractionError, "differs"):
            upstream.extract(archive, destination, digest)

    def test_integrity_check_and_extraction_are_pinned_to_one_descriptor(self):
        # Red-team finding (pass 2): hashing the archive by path and then re-opening the
        # same path for tarfile leaves a TOCTOU window -- the verified bytes and the
        # extracted bytes could diverge. Extraction must read the exact descriptor that
        # was hashed. We swap the archive path's content immediately after the hash is
        # taken; a double-open would extract the swapped payload (or hash-mismatch),
        # while a pinned descriptor must still extract the confirmed archive.
        import os
        if os.name == "nt":
            self.skipTest("POSIX os.replace over an already-open descriptor")
        archive_a, digest_a = self.archive([
            ("package/package.json", "file", json.dumps({"name": "openclaw", "version": "1.0.0"})),
            ("package/marker.txt", "file", "authentic-A"),
        ])
        archive_b, _digest_b = self.archive([
            ("package/package.json", "file", json.dumps({"name": "evil", "version": "9.9.9"})),
            ("package/marker.txt", "file", "swapped-B"),
        ])
        destination = self.root / "pinned"
        real_stream_digest = upstream._stream_digest

        def swap_after_hash(reader):
            value = real_stream_digest(reader)
            os.replace(archive_b, archive_a)  # repoint the path at B after A is hashed
            return value

        upstream._stream_digest = swap_after_hash
        try:
            result = upstream.extract(archive_a, destination, digest_a)
        finally:
            upstream._stream_digest = real_stream_digest
        self.assertEqual((destination / "marker.txt").read_text(), "authentic-A")
        self.assertEqual((destination / "package.json").read_text(), json.dumps({"name": "openclaw", "version": "1.0.0"}))
        self.assertEqual(result["archiveSha256"], digest_a)

    def test_traversal_and_links_are_rejected(self):
        traversal, traversal_digest = self.archive([
            ("package/package.json", "file", "{}"),
            ("package/../escape", "file", "bad"),
        ])
        with self.assertRaisesRegex(upstream.ExtractionError, "unsafe"):
            upstream.extract(traversal, self.root / "traversal", traversal_digest)
        linked, linked_digest = self.archive([
            ("package/package.json", "file", "{}"),
            ("package/link", "symlink", "/etc/passwd"),
        ])
        with self.assertRaisesRegex(upstream.ExtractionError, "unsupported"):
            upstream.extract(linked, self.root / "linked", linked_digest)


if __name__ == "__main__":
    unittest.main()
