"""Verify resolve_credential rejects negative or out-of-range revisions."""
import unittest
import sys
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.store import StoreError
from pixel_provider.vault import CredentialStore

class VaultExpectedRevisionBoundsTests(unittest.TestCase):
    def test_out_of_bounds_revisions_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CredentialStore(Path(tmp))
            for bad_rev in (-1, 2**53, True):
                with self.assertRaises(StoreError) as ctx:
                    store.resolve_credential("p1", expected_revision=bad_rev)
                self.assertEqual(str(ctx.exception), "invalid-request")

if __name__ == "__main__":
    unittest.main()
