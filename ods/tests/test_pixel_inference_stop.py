"""Verify pixel-inference validates stop parameter constraints."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))
sys.path.insert(0, str(repo_root / "extensions/services/pixel-inference/app"))

from main import _prepare, ShareError

class PixelInferenceStopTests(unittest.TestCase):
    def setUp(self):
        self.grant = {"maxOutputTokens": 2048}
        self.base_payload = {
            "model": "ods/shared",
            "messages": [{"role": "user", "content": "hello"}],
        }

    def test_valid_stop(self):
        for valid in ("stop_seq", ["stop1", "stop2"], []):
            payload = dict(self.base_payload, stop=valid)
            prepared = _prepare(payload, self.grant)
            self.assertEqual(prepared["stop"], valid)

    def test_invalid_stop_rejected(self):
        for bad in (123, True, ["s1", 2], ["s1", "s2", "s3", "s4", "s5"]):
            payload = dict(self.base_payload, stop=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.code, "invalid_stop")

if __name__ == "__main__":
    unittest.main()
