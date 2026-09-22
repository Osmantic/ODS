"""Verify scopes handle rejects unsupported actions without modifying filesystem."""
import unittest
import sys
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.store import StoreError
from pixel_provider.scopes import handle

class ScopesHandleActionTests(unittest.TestCase):
    def test_unsupported_action_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "nonexistent"
            for bad in (None, "delete", "invalid", 123):
                with self.assertRaises(StoreError) as ctx:
                    handle(str(data_dir), bad, {})
                self.assertEqual(str(ctx.exception), "invalid-scope-request")
            self.assertFalse(data_dir.exists())

if __name__ == "__main__":
    unittest.main()
