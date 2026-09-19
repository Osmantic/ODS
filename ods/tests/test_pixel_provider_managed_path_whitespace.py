"""Verify _path rejects path strings with unstripped whitespace."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.managed_deployment import _path
from pixel_provider.store import StoreError

class ManagedPathWhitespaceTests(unittest.TestCase):
    def test_unstripped_path_rejected(self):
        for bad in (" /usr/bin/python3", "/usr/bin/python3 ", " /usr/bin/python3 "):
            with self.assertRaises(StoreError) as ctx:
                _path(bad)
            self.assertEqual(str(ctx.exception), "invalid-managed-deployment")

    def test_clean_path_accepted(self):
        self.assertEqual(_path("/usr/bin/python3"), "/usr/bin/python3")

if __name__ == "__main__":
    unittest.main()
