import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from project_snapshot import UnsafeProjectSource, snapshot_project
import project_snapshot


@unittest.skipUnless(os.name == "posix", "snapshot executes in POSIX project broker")
class ProjectSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "projeto"
        self.project.mkdir()
        (self.project / "package.json").write_text('{"private":true}')

    def test_stable_bytes_and_secret_cache_exclusion(self):
        (self.project / ".env.local").write_text("PRIVATE")
        (self.project / ".npmrc").write_text("TOKEN")
        (self.project / "node_modules").mkdir()
        (self.project / "node_modules" / "secret").write_text("PRIVATE")
        (self.project / "página.tsx").write_text("exact\n", encoding="utf-8")
        result = snapshot_project(str(self.root), "projeto")
        self.assertEqual(result["sha256"], snapshot_project(str(self.root), "projeto")["sha256"])
        self.assertEqual(result["files"]["página.tsx"], b"exact\n")
        self.assertEqual(set(result["omitted"]), {".env.local", ".npmrc", "node_modules"})
        with tarfile.open(fileobj=io.BytesIO(result["archive"])) as archive:
            self.assertEqual(set(archive.getnames()), {"package.json", "página.tsx"})
            self.assertTrue(all(m.isfile() and m.uid == 1000 for m in archive))

    def test_rejects_links_in_project_or_selected_path(self):
        target = self.root / "outside"
        target.write_text("PRIVATE")
        (self.project / "escape").symlink_to(target)
        with self.assertRaises(OSError):
            snapshot_project(str(self.root), "projeto")
        (self.root / "alias").symlink_to(self.project, target_is_directory=True)
        with self.assertRaises(OSError):
            snapshot_project(str(self.root), "alias")

    def test_rejects_hardlinks_and_traversal(self):
        target = self.root / "outside"
        target.write_text("PRIVATE")
        os.link(target, self.project / "linked")
        with self.assertRaises(UnsafeProjectSource):
            snapshot_project(str(self.root), "projeto")
        for path in ("../projeto", "/projeto", "projeto/..", "C:\\projeto", ""):
            with self.assertRaises(UnsafeProjectSource):
                snapshot_project(str(self.root), path)

    def test_rejects_oversized_file_before_copying(self):
        with (self.project / "huge.bin").open("wb") as output:
            output.truncate(project_snapshot.MAX_FILE + 1)
        with self.assertRaises(UnsafeProjectSource):
            snapshot_project(str(self.root), "projeto")


if __name__ == "__main__":
    unittest.main()
