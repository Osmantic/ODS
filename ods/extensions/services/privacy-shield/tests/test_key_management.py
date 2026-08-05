import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Allow running this test from repo root without installing the service as a package.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from key_management import load_persisted_key, resolve_shield_api_key, persist_key


class TestKeyManagement(unittest.TestCase):
    def test_env_key_wins(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "shield_api_key")
            persist_key(key_path, "persisted")
            self.assertEqual(resolve_shield_api_key("from_env", key_path), "from_env")

    def test_loads_persisted_key(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "shield_api_key")
            persist_key(key_path, "persisted")
            self.assertEqual(resolve_shield_api_key(None, key_path), "persisted")

    def test_generates_and_persists_key(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "shield_api_key")
            key = resolve_shield_api_key(None, key_path)
            self.assertTrue(isinstance(key, str) and len(key) > 0)
            with open(key_path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read().strip(), key)

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not enforced on Windows")
    def test_persisted_key_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            key_path = os.path.join(d, "shield_api_key")

            persist_key(key_path, "persisted")

            self.assertEqual(os.stat(key_path).st_mode & 0o777, 0o600)

    def test_read_errors_are_not_treated_as_missing_keys(self):
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                load_persisted_key("/unreadable/shield_api_key")

    def test_persist_errors_do_not_return_ephemeral_keys(self):
        with mock.patch(
            "key_management.persist_key", side_effect=PermissionError("denied")
        ):
            with self.assertRaises(PermissionError):
                resolve_shield_api_key(None, "/unwritable/shield_api_key")


if __name__ == "__main__":
    unittest.main()
