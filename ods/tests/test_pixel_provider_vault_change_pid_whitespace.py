"""Verify validate_edit rejects credential change provider IDs with unstripped whitespace."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.store import StoreError
from pixel_provider.vault import validate_edit

class VaultChangePidWhitespaceTests(unittest.TestCase):
    def test_whitespace_pid_in_changes_rejected(self):
        body = {
            "expectedRevision": 0,
            "document": {
                "revision": 0,
                "providers": [{"id": "p1", "hasCredential": True}]
            },
            "credentialChanges": {
                " p1 ": {"action": "remove"}
            }
        }
        with self.assertRaises(StoreError) as ctx:
            validate_edit(body)
        self.assertEqual(str(ctx.exception), "invalid-request")

if __name__ == "__main__":
    unittest.main()
