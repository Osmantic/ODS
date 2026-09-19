"""Verify probe_provider safely handles malformed provider dicts without unhandled KeyError."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_provider.health import probe_provider

class ProbeProviderKeyErrorTests(unittest.TestCase):
    def test_missing_keys_handled_safely(self):
        for bad in ({}, {"id": "p1"}, None):
            res = probe_provider(bad, None)
            self.assertEqual(res, {"status": "offline"})

if __name__ == "__main__":
    unittest.main()
