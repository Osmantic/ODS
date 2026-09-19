#!/usr/bin/env python3
"""Regression test: pixel-inference validates stop parameter type.

The baseline admitted non-string and non-list stop arguments (such as integers,
mappings, booleans, or lists containing non-string items), causing backend JSON
serialization or runtime model inference errors.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "bin"))
sys.path.insert(0, str(REPO_ROOT / "extensions/services/pixel-inference/app"))

import main
from main import ShareError, _prepare, PUBLIC_MODEL


class PixelInferenceStopTypeTests(unittest.TestCase):
    def setUp(self):
        self.grant = {"maxOutputTokens": 2048}
        self.base_payload = {
            "model": PUBLIC_MODEL,
            "messages": [{"role": "user", "content": "hello"}],
        }

    def test_valid_stop_values_accepted(self):
        for val in (None, "\n", "stop", ["\n"], ["<|im_end|>", "END"]):
            payload = dict(self.base_payload, stop=val)
            prepared = _prepare(payload, self.grant)
            self.assertEqual(prepared["stop"], val)

    def test_invalid_scalar_stop_rejected(self):
        for bad in (123, 0, True, False, {"stop": "now"}, (1, 2)):
            payload = dict(self.base_payload, stop=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.status, 400)
            self.assertEqual(ctx.exception.code, "invalid_stop")

    def test_invalid_list_items_stop_rejected(self):
        for bad in ([123], ["valid", 456], [None], [True]):
            payload = dict(self.base_payload, stop=bad)
            with self.assertRaises(ShareError) as ctx:
                _prepare(payload, self.grant)
            self.assertEqual(ctx.exception.status, 400)
            self.assertEqual(ctx.exception.code, "invalid_stop")


if __name__ == "__main__":
    unittest.main()
