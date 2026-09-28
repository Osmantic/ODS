#!/usr/bin/env python3
"""Regression test: runtime gateway validates presence and frequency penalty bounds [-2.0, 2.0]."""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from pixel_provider.runtime_gateway import MODEL, RuntimeErrorCode, validate_request


class TestPixelGatewayPenalties(unittest.TestCase):
    def base_payload(self, **kwargs):
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": "hello"}],
        }
        payload.update(kwargs)
        return payload

    def test_valid_penalties_accepted(self):
        for param in ("presence_penalty", "frequency_penalty"):
            for val in (-2.0, -1.0, 0.0, 1.5, 2.0, -2, 2):
                p = self.base_payload(**{param: val})
                res = validate_request(p)
                self.assertEqual(res[param], val)

    def test_invalid_penalties_out_of_bounds_rejected(self):
        for param in ("presence_penalty", "frequency_penalty"):
            for val in (-2.01, -3.0, 2.01, 5.0, 100):
                p = self.base_payload(**{param: val})
                with self.assertRaises(RuntimeErrorCode):
                    validate_request(p)

    def test_invalid_penalty_types_rejected(self):
        for param in ("presence_penalty", "frequency_penalty"):
            for val in ("0.5", True, False, None, [], {}):
                p = self.base_payload(**{param: val})
                with self.assertRaises(RuntimeErrorCode):
                    validate_request(p)


if __name__ == "__main__":
    unittest.main()
