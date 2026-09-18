"""Verify pixel-inference enforces positive integer bounds on seed."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))
sys.path.insert(0, str(repo_root / "extensions/services/pixel-inference/app"))

from main import _prepare, ShareError

class PixelInferenceSeedTests(unittest.TestCase):
    def setUp(self):
        self.grant = {"maxOutputTokens": 2048}
        self.base_payload = {
            "model": "ods/shared",
            "messages": [{"role": "user", "content": "hello"}],
        }

    def test_valid_seeds(self):
        for s in (0, 42, 123456789, 2**63 - 1):
            payload = dict(self.base_payload, seed=s)
            prepared = _prepare(payload, self.grant)
            self.assertEqual(prepared["seed"], s)

    def test_invalid_seeds_rejected(self):
        for bad in (-1, -42, 2**63, 1.5, "42", True, False):
            payload = dict(self.base_payload, seed=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.code, "invalid_seed")

if __name__ == "__main__":
    unittest.main()
