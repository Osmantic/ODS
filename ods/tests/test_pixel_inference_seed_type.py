#!/usr/bin/env python3
"""Regression test: pixel-inference rejects boolean and non-integer seed values.

In Python, bool is a subclass of int, so naive isinstance(val, int) checks admit
True/False as integer seeds (1 and 0). Furthermore, float or string seeds cause
downstream RNG seeding failures.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "bin"))
sys.path.insert(0, str(REPO_ROOT / "extensions/services/pixel-inference/app"))

import main
from main import ShareError, _prepare, PUBLIC_MODEL


class PixelInferenceSeedTypeTests(unittest.TestCase):
    def setUp(self):
        self.grant = {"maxOutputTokens": 2048}
        self.base_payload = {
            "model": PUBLIC_MODEL,
            "messages": [{"role": "user", "content": "hello"}],
        }

    def test_valid_seed_values_accepted(self):
        for val in (None, 0, 1, 42, 123456789, -1):
            payload = dict(self.base_payload, seed=val)
            prepared = _prepare(payload, self.grant)
            self.assertEqual(prepared["seed"], val)

    def test_boolean_seeds_rejected(self):
        for bad in (True, False):
            payload = dict(self.base_payload, seed=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.status, 400)
            self.assertEqual(ctx.exception.code, "invalid_seed")

    def test_non_integer_seeds_rejected(self):
        for bad in (42.0, "42", [42], {"seed": 42}):
            payload = dict(self.base_payload, seed=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.status, 400)
            self.assertEqual(ctx.exception.code, "invalid_seed")


if __name__ == "__main__":
    unittest.main()
