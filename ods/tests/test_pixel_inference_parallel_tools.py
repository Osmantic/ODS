"""Verify pixel-inference rejects non-boolean parallel_tool_calls."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))
sys.path.insert(0, str(repo_root / "extensions/services/pixel-inference/app"))

from main import _prepare, ShareError

class PixelInferenceParallelToolCallsTests(unittest.TestCase):
    def setUp(self):
        self.grant = {"maxOutputTokens": 2048}
        self.base_payload = {
            "model": "ods/shared",
            "messages": [{"role": "user", "content": "hello"}],
        }

    def test_valid_parallel_tool_calls(self):
        for val in (True, False):
            payload = dict(self.base_payload, parallel_tool_calls=val)
            prepared = _prepare(payload, self.grant)
            self.assertEqual(prepared["parallel_tool_calls"], val)

    def test_invalid_parallel_tool_calls_rejected(self):
        for bad in (1, 0, "true", "false", None, []):
            payload = dict(self.base_payload, parallel_tool_calls=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.code, "invalid_parallel_tool_calls")

if __name__ == "__main__":
    unittest.main()
