"""Verify pixel-inference validates stream_options structure."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))
sys.path.insert(0, str(repo_root / "extensions/services/pixel-inference/app"))

from main import _prepare, ShareError

class PixelInferenceStreamOptionsTests(unittest.TestCase):
    def setUp(self):
        self.grant = {"maxOutputTokens": 2048}
        self.base_payload = {
            "model": "ods/shared",
            "messages": [{"role": "user", "content": "hello"}],
        }

    def test_valid_stream_options(self):
        for opts in ({}, {"include_usage": True}, {"include_usage": False}):
            payload = dict(self.base_payload, stream_options=opts)
            prepared = _prepare(payload, self.grant)
            self.assertEqual(prepared["stream_options"], opts)

    def test_invalid_stream_options_rejected(self):
        for bad in (True, "include_usage", [1], {"include_usage": "yes"}, {"unknown": True}):
            payload = dict(self.base_payload, stream_options=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.code, "unsupported_stream_options")

if __name__ == "__main__":
    unittest.main()
