#!/usr/bin/env python3
"""Regression test: runtime gateway validates logprobs boolean and top_logprobs integer bounds."""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from pixel_provider.runtime_gateway import MODEL, RuntimeErrorCode, validate_request


class TestPixelGatewayLogprobsBounds(unittest.TestCase):
    def base_payload(self, **kwargs):
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": "hello"}],
        }
        payload.update(kwargs)
        return payload

    def test_valid_logprobs_accepted(self):
        p1 = self.base_payload(logprobs=True)
        self.assertTrue(validate_request(p1)["logprobs"])

        p2 = self.base_payload(logprobs=False)
        self.assertFalse(validate_request(p2)["logprobs"])

        p3 = self.base_payload(logprobs=True, top_logprobs=5)
        self.assertEqual(validate_request(p3)["top_logprobs"], 5)

    def test_top_logprobs_without_logprobs_true_rejected(self):
        # top_logprobs requires logprobs=True
        p1 = self.base_payload(top_logprobs=5)
        with self.assertRaises(RuntimeErrorCode):
            validate_request(p1)

        p2 = self.base_payload(logprobs=False, top_logprobs=5)
        with self.assertRaises(RuntimeErrorCode):
            validate_request(p2)

    def test_invalid_top_logprobs_bounds_rejected(self):
        for val in (-1, 21, 100):
            p = self.base_payload(logprobs=True, top_logprobs=val)
            with self.assertRaises(RuntimeErrorCode):
                validate_request(p)

    def test_invalid_logprobs_types_rejected(self):
        for val in ("true", 1, 0, None, [], {}):
            p = self.base_payload(logprobs=val)
            with self.assertRaises(RuntimeErrorCode):
                validate_request(p)


if __name__ == "__main__":
    unittest.main()
