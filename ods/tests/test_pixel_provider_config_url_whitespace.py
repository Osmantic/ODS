"""Verify _validate_base_url rejects URL strings with surrounding whitespace."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.config import _validate_base_url, ConfigError

class ConfigBaseUrlWhitespaceTests(unittest.TestCase):
    def test_unstripped_url_rejected(self):
        for bad in (" http://127.0.0.1:8000/v1", "http://127.0.0.1:8000/v1 ", " http://127.0.0.1:8000/v1 "):
            with self.assertRaises(ConfigError) as ctx:
                _validate_base_url(bad, "test")
            self.assertEqual(ctx.exception.code, "invalid_url")

    def test_clean_url_accepted(self):
        res = _validate_base_url("http://127.0.0.1:8000/v1", "test")
        self.assertEqual(res, "http://127.0.0.1:8000/v1")

if __name__ == "__main__":
    unittest.main()
