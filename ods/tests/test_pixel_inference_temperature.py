"""Verify pixel-inference enforces [0.0, 2.0] bounds on temperature."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))
sys.path.insert(0, str(repo_root / "extensions/services/pixel-inference/app"))

from main import _prepare, ShareError

class PixelInferenceTemperatureTests(unittest.TestCase):
    def setUp(self):
        self.grant = {"maxOutputTokens": 2048}
        self.base_payload = {
            "model": "ods/shared",
            "messages": [{"role": "user", "content": "hello"}],
        }

    def test_valid_temperatures(self):
        for temp in (0, 0.0, 0.7, 1.0, 2, 2.0):
            payload = dict(self.base_payload, temperature=temp)
            prepared = _prepare(payload, self.grant)
            self.assertEqual(prepared["temperature"], temp)

    def test_invalid_temperatures_rejected(self):
        for bad in (-0.1, 2.1, float('nan'), float('inf'), True, False, "0.7"):
            payload = dict(self.base_payload, temperature=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.code, "invalid_temperature")

if __name__ == "__main__":
    unittest.main()
