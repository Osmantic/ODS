"""Verify pixel-inference validates presence_penalty and frequency_penalty."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))
sys.path.insert(0, str(repo_root / "extensions/services/pixel-inference/app"))

from main import _prepare, ShareError

class PixelInferencePenaltiesTests(unittest.TestCase):
    def setUp(self):
        self.grant = {"maxOutputTokens": 2048}
        self.base_payload = {
            "model": "ods/shared",
            "messages": [{"role": "user", "content": "hello"}],
        }

    def test_valid_penalties(self):
        for pen in (-2.0, -1, 0, 0.5, 2, 2.0):
            payload = dict(self.base_payload, presence_penalty=pen, frequency_penalty=pen)
            prepared = _prepare(payload, self.grant)
            self.assertEqual(prepared["presence_penalty"], pen)
            self.assertEqual(prepared["frequency_penalty"], pen)

    def test_invalid_penalties_rejected(self):
        for bad in (-2.1, 2.1, float('nan'), float('inf'), True, "1.0"):
            with self.assertRaises(ShareError):
                _prepare(dict(self.base_payload, presence_penalty=bad), self.grant)
            with self.assertRaises(ShareError):
                _prepare(dict(self.base_payload, frequency_penalty=bad), self.grant)

if __name__ == "__main__":
    unittest.main()
