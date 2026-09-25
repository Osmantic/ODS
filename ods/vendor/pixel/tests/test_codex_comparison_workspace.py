import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pixel_test_codex_comparison_workspace", ROOT / "scripts/codex_comparison_workspace.py",
)
workspace = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(workspace)


class CodexComparisonWorkspaceTests(unittest.TestCase):
    def test_exact_tree_copy_preserves_content_and_enforces_byte_ceiling(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "nested").mkdir()
            (source / "nested/data.txt").write_bytes(b"bounded\n")
            receipt = workspace.inventory_and_copy(source, destination, 1024)
            self.assertEqual(receipt["entries"], 2)
            self.assertEqual(receipt["bytes"], 8)
            self.assertEqual((destination / "nested/data.txt").read_bytes(), b"bounded\n")
            second = root / "second"
            second.mkdir()
            with self.assertRaisesRegex(workspace.WorkspaceError, "disk ceiling"):
                workspace.inventory_and_copy(source, second, 7)
            self.assertEqual(list(second.iterdir()), [])

    def test_copy_rejects_nonempty_destination_and_symbolic_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            destination.mkdir()
            (destination / "occupied").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(workspace.WorkspaceError, "not empty"):
                workspace.inventory_and_copy(source, destination, 1024)
            (destination / "occupied").unlink()
            target = source / "target.txt"
            target.write_text("x", encoding="utf-8")
            link = source / "link.txt"
            try:
                link.symlink_to(target)
            except OSError:
                return
            with self.assertRaisesRegex(workspace.WorkspaceError, "symbolic link"):
                workspace.inventory_and_copy(source, destination, 1024)
            self.assertEqual(list(destination.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
