"""Verify pixel-inference enforces [0.0, 1.0] bounds on top_p."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))
sys.path.insert(0, str(repo_root / "extensions/services/pixel-inference/app"))

from main import _prepare, ShareError

class PixelInferenceTopPTests(unittest.TestCase):
    def setUp(self):
        self.grant = {"maxOutputTokens": 2048}
        self.base_payload = {
            "model": "ods/shared",
            "messages": [{"role": "user", "content": "hello"}],
        }

    def test_valid_top_p(self):
        for p in (0, 0.0, 0.5, 0.95, 1, 1.0):
            payload = dict(self.base_payload, top_p=p)
            prepared = _prepare(payload, self.grant)
            self.assertEqual(prepared["top_p"], p)

    def test_invalid_top_p_rejected(self):
        for bad in (-0.01, 1.01, float('nan'), float('inf'), True, False, "0.9"):
            payload = dict(self.base_payload, top_p=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.code, "invalid_top_p")

if __name__ == "__main__":
    unittest.main()
