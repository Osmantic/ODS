"""Verify owner_namespace rejects empty or unstripped credentials."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/dashboard-api"))

from pixel_chat_results import owner_namespace

class OwnerNamespaceTests(unittest.TestCase):
    def test_empty_credential_rejected(self):
        with self.assertRaises(ValueError):
            owner_namespace("")

    def test_unstripped_credential_rejected(self):
        for bad in (" owner-key ", " owner-key", "owner-key "):
            with self.assertRaises(ValueError):
                owner_namespace(bad)

    def test_clean_credential_accepted(self):
        ns = owner_namespace("ods-owner-key-12345")
        self.assertEqual(len(ns), 64)

if __name__ == "__main__":
    unittest.main()
