"""Verify sharing authenticate rejects invalid or negative now timestamps."""
import unittest
import sys
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.store import StoreError
from pixel_provider.sharing import SharingStore

class SharingAuthNowBoundsTests(unittest.TestCase):
    def test_invalid_now_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharingStore(Path(tmp))
            token = "ods_infer_" + "0" * 64
            for bad_now in (-1, 2**53, "invalid", True):
                with self.assertRaises(StoreError) as ctx:
                    store.authenticate(token, now=bad_now)
                self.assertEqual(str(ctx.exception), "invalid-credential")

if __name__ == "__main__":
    unittest.main()
