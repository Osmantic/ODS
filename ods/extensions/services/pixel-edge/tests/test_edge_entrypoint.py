from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edge_entrypoint import provision_empty_volume
from transition_gate import GateError


class ProvisionTests(unittest.TestCase):
    def fixture(self):
        return Path(tempfile.mkdtemp(prefix="ods-edge-volume-test-"))

    def test_new_private_volume_initialized_once(self):
        path = self.fixture()
        provision_empty_volume(str(path))
        original = {file.name: file.read_bytes() for file in path.iterdir()}
        self.assertEqual(len(original), 2)
        provision_empty_volume(str(path))
        self.assertEqual({file.name: file.read_bytes() for file in path.iterdir()}, original)
        for file in path.iterdir(): self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_partial_or_corrupt_volume_never_reset(self):
        path = self.fixture()
        marker = path / "state.json"
        marker.write_text("retained corrupt evidence")
        provision_empty_volume(str(path))
        self.assertEqual(marker.read_text(), "retained corrupt evidence")
        self.assertEqual(list(path.iterdir()), [marker])

    def test_unsafe_empty_directory_rejected_without_new_files(self):
        path = self.fixture()
        path.chmod(0o755)
        with self.assertRaises(GateError): provision_empty_volume(str(path))
        self.assertEqual(list(path.iterdir()), [])

    def test_symlink_to_empty_directory_rejected(self):
        path = self.fixture()
        link = path.parent / (path.name + "-link")
        link.symlink_to(path, target_is_directory=True)
        with self.assertRaises(GateError): provision_empty_volume(str(link))
        self.assertEqual(list(path.iterdir()), [])


if __name__ == "__main__": unittest.main()
