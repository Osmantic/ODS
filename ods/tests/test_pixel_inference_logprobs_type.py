#!/usr/bin/env python3
"""Regression test: pixel-inference rejects non-boolean logprobs values.

The OpenAI chat completions specification defines logprobs as an optional boolean.
Passing integer, string, or complex values causes downstream inference failures or
unexpected log probability computation states.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "bin"))
sys.path.insert(0, str(REPO_ROOT / "extensions/services/pixel-inference/app"))

import main
from main import ShareError, _prepare, PUBLIC_MODEL


class PixelInferenceLogprobsTypeTests(unittest.TestCase):
    def setUp(self):
        self.grant = {"maxOutputTokens": 2048}
        self.base_payload = {
            "model": PUBLIC_MODEL,
            "messages": [{"role": "user", "content": "hello"}],
        }

    def test_valid_logprobs_accepted(self):
        for val in (None, True, False):
            payload = dict(self.base_payload, logprobs=val)
            prepared = _prepare(payload, self.grant)
            self.assertEqual(prepared["logprobs"], val)

    def test_integer_logprobs_rejected(self):
        for bad in (0, 1, 2, 5, -1):
            payload = dict(self.base_payload, logprobs=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.status, 400)
            self.assertEqual(ctx.exception.code, "invalid_logprobs")

    def test_non_boolean_logprobs_rejected(self):
        for bad in ("true", "false", 1.0, [True], {"enabled": True}):
            payload = dict(self.base_payload, logprobs=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.status, 400)
            self.assertEqual(ctx.exception.code, "invalid_logprobs")


if __name__ == "__main__":
    unittest.main()
