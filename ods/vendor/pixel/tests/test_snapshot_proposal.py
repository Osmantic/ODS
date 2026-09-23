import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "snapshot-proposal.py"
SPEC = importlib.util.spec_from_file_location("pixel_snapshot_proposal", MODULE_PATH)
snapshotter = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(snapshotter)


class ProposalSnapshotTests(unittest.TestCase):
    def test_regular_proposal_is_copied_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "proposal.json", root / "snapshot.json"
            source.write_bytes(b'{"proposalId":"fixture"}\n')
            if os.name != "nt": source.chmod(0o600)
            output.touch(mode=0o600)
            copied = snapshotter.snapshot(source, output)
            self.assertEqual(copied, source.stat().st_size)
            self.assertEqual(output.read_bytes(), source.read_bytes())

    @unittest.skipIf(os.name == "nt", "Windows symlink creation requires optional privilege")
    def test_symlink_source_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, source, output = root / "target", root / "proposal", root / "output"
            target.write_text("secret", encoding="utf-8")
            source.symlink_to(target)
            output.touch(mode=0o600)
            with self.assertRaises((snapshotter.SnapshotError, OSError)):
                snapshotter.snapshot(source, output)

    def test_oversized_source_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "proposal", root / "output"
            source.write_bytes(b"x" * (snapshotter.MAX_BYTES + 1))
            if os.name != "nt": source.chmod(0o600)
            output.touch(mode=0o600)
            with self.assertRaisesRegex(snapshotter.SnapshotError, "bounded"):
                snapshotter.snapshot(source, output)

    def test_nonempty_or_linked_output_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "proposal", root / "output"
            source.write_text("safe", encoding="utf-8")
            if os.name != "nt": source.chmod(0o600)
            output.write_text("existing", encoding="utf-8")
            with self.assertRaisesRegex(snapshotter.SnapshotError, "new empty"):
                snapshotter.snapshot(source, output)


if __name__ == "__main__":
    unittest.main()
