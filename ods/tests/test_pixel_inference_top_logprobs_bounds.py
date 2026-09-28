#!/usr/bin/env python3
"""Regression test: pixel-inference rejects out-of-range, boolean, and non-integer top_logprobs.

The OpenAI chat completions specification defines top_logprobs as an integer between 0 and 5.
Out-of-bounds integers, booleans (which inherit from int in Python), and float/string values
cause downstream engine crashes or invalid log probability distribution arrays.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "bin"))
sys.path.insert(0, str(REPO_ROOT / "extensions/services/pixel-inference/app"))

import main
from main import ShareError, _prepare, PUBLIC_MODEL


class PixelInferenceTopLogprobsBoundsTests(unittest.TestCase):
    def setUp(self):
        self.grant = {"maxOutputTokens": 2048}
        self.base_payload = {
            "model": PUBLIC_MODEL,
            "messages": [{"role": "user", "content": "hello"}],
        }

    def test_valid_top_logprobs_accepted(self):
        for val in (None, 0, 1, 2, 3, 4, 5):
            payload = dict(self.base_payload, top_logprobs=val)
            prepared = _prepare(payload, self.grant)
            self.assertEqual(prepared["top_logprobs"], val)

    def test_out_of_bounds_top_logprobs_rejected(self):
        for bad in (-1, -5, 6, 10, 100):
            payload = dict(self.base_payload, top_logprobs=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.status, 400)
            self.assertEqual(ctx.exception.code, "invalid_top_logprobs")

    def test_boolean_top_logprobs_rejected(self):
        for bad in (True, False):
            payload = dict(self.base_payload, top_logprobs=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.status, 400)
            self.assertEqual(ctx.exception.code, "invalid_top_logprobs")

    def test_non_integer_top_logprobs_rejected(self):
        for bad in (2.5, "3", [2], {"k": 1}):
            payload = dict(self.base_payload, top_logprobs=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.status, 400)
            self.assertEqual(ctx.exception.code, "invalid_top_logprobs")


if __name__ == "__main__":
    unittest.main()
