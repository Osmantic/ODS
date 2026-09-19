"""Verify sharing revoke enforces canonical device id format before file lock."""
import unittest
import sys
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.store import StoreError
from pixel_provider.sharing import SharingStore

class SharingRevokeDeviceFormatTests(unittest.TestCase):
    def test_invalid_device_ids_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharingStore(Path(tmp))
            for bad in (None, 123, "device-short", "device-" + "g" * 16, "device-" + "0" * 15):
                with self.assertRaises(StoreError) as ctx:
                    store.revoke(bad, expected_revision=0)
                self.assertEqual(str(ctx.exception), "invalid-request")

if __name__ == "__main__":
    unittest.main()
