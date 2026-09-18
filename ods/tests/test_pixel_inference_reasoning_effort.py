"""Verify pixel-inference restricts reasoning_effort to valid choices."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))
sys.path.insert(0, str(repo_root / "extensions/services/pixel-inference/app"))

from main import _prepare, ShareError

class PixelInferenceReasoningEffortTests(unittest.TestCase):
    def setUp(self):
        self.grant = {"maxOutputTokens": 2048}
        self.base_payload = {
            "model": "ods/shared",
            "messages": [{"role": "user", "content": "hello"}],
        }

    def test_valid_reasoning_effort(self):
        for choice in ('low', 'medium', 'high'):
            payload = dict(self.base_payload, reasoning_effort=choice)
            prepared = _prepare(payload, self.grant)
            self.assertEqual(prepared["reasoning_effort"], choice)

    def test_invalid_reasoning_effort_rejected(self):
        for bad in ('minimal', 'max', 'none', '', 123, True):
            payload = dict(self.base_payload, reasoning_effort=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.code, "invalid_reasoning_effort")

if __name__ == "__main__":
    unittest.main()
