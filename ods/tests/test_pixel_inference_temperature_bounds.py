#!/usr/bin/env python3
"""Regression test: pixel-inference rejects negative or out-of-range temperature.

The baseline accepted negative temperature (-1.0), values exceeding 2.0 (3.5),
and non-numeric/boolean types (True, 'hot') without validation, causing downstream
model server failures or unhandled exceptions.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "bin"))
sys.path.insert(0, str(REPO_ROOT / "extensions/services/pixel-inference/app"))

import main
from main import ShareError, _prepare, PUBLIC_MODEL


class PixelInferenceTemperatureBoundsTests(unittest.TestCase):
    def setUp(self):
        self.grant = {"maxOutputTokens": 2048}
        self.base_payload = {
            "model": PUBLIC_MODEL,
            "messages": [{"role": "user", "content": "hello"}],
        }

    def test_valid_temperatures_accepted(self):
        for val in (0, 0.0, 0.7, 1.0, 1.5, 2, 2.0):
            payload = dict(self.base_payload, temperature=val)
            prepared = _prepare(payload, self.grant)
            self.assertEqual(prepared["temperature"], val)

    def test_negative_temperature_rejected(self):
        for bad in (-0.1, -1, -1.0, -10):
            payload = dict(self.base_payload, temperature=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.status, 400)
            self.assertEqual(ctx.exception.code, "invalid_temperature")

    def test_exceeded_temperature_rejected(self):
        for bad in (2.01, 2.5, 3, 10):
            payload = dict(self.base_payload, temperature=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.status, 400)
            self.assertEqual(ctx.exception.code, "invalid_temperature")

    def test_non_numeric_and_bool_temperature_rejected(self):
        for bad in (True, False, "0.7", [0.7], {"temp": 1}):
            payload = dict(self.base_payload, temperature=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.status, 400)
            self.assertEqual(ctx.exception.code, "invalid_temperature")


if __name__ == "__main__":
    unittest.main()
