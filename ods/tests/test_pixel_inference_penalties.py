#!/usr/bin/env python3
"""Regression test: pixel-inference validates presence_penalty and frequency_penalty bounds.

The baseline accepted out-of-range penalties (e.g. 5.0, -10.0) and non-numeric/boolean
values (True, 'high') without validation, passing them to downstream model runtimes
where they cause unhandled errors or invalid inference states.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "bin"))
sys.path.insert(0, str(REPO_ROOT / "extensions/services/pixel-inference/app"))

import main
from main import ShareError, _prepare, PUBLIC_MODEL


class PixelInferencePenaltiesTests(unittest.TestCase):
    def setUp(self):
        self.grant = {"maxOutputTokens": 2048}
        self.base_payload = {
            "model": PUBLIC_MODEL,
            "messages": [{"role": "user", "content": "hello"}],
        }

    def test_valid_penalties_accepted(self):
        for penalty in ("presence_penalty", "frequency_penalty"):
            for val in (-2.0, -1.0, 0, 0.0, 0.5, 1.5, 2.0):
                payload = dict(self.base_payload, **{penalty: val})
                prepared = _prepare(payload, self.grant)
                self.assertEqual(prepared[penalty], val)

    def test_out_of_range_penalties_rejected(self):
        for penalty in ("presence_penalty", "frequency_penalty"):
            for bad in (-2.01, -3.0, 2.01, 5.0, 10):
                payload = dict(self.base_payload, **{penalty: bad})
                with self.assertRaises(ShareError) as ctx:
                    _prepare(payload, self.grant)
                self.assertEqual(ctx.exception.status, 400)
                self.assertEqual(ctx.exception.code, f"invalid_{penalty}")

    def test_non_numeric_and_bool_penalties_rejected(self):
        for penalty in ("presence_penalty", "frequency_penalty"):
            for bad in (True, False, "1.0", [0.5], {"val": 1}):
                payload = dict(self.base_payload, **{penalty: bad})
                with self.assertRaises(ShareError) as ctx:
                    _prepare(payload, self.grant)
                self.assertEqual(ctx.exception.status, 400)
                self.assertEqual(ctx.exception.code, f"invalid_{penalty}")


if __name__ == "__main__":
    unittest.main()
