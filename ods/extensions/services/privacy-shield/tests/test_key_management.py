import os
import sys
import tempfile
import unittest

# Allow running this test from repo root without installing the service as a package.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from key_management import resolve_shield_api_key, persist_key


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


if __name__ == "__main__":
    unittest.main()
