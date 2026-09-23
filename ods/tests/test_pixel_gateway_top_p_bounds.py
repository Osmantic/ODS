#!/usr/bin/env python3
"""Regression test: runtime gateway validates top_p float bounds [0.0, 1.0]."""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from pixel_provider.runtime_gateway import MODEL, RuntimeErrorCode, validate_request


class TestPixelGatewayTopPBounds(unittest.TestCase):
    def base_payload(self, **kwargs):
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": "hello"}],
        }
        payload.update(kwargs)
        return payload

    def test_valid_top_p_accepted(self):
        for val in (0.0, 0.5, 1.0, 0, 1):
            p = self.base_payload(top_p=val)
            res = validate_request(p)
            self.assertEqual(res["top_p"], val)

    def test_invalid_top_p_out_of_bounds_rejected(self):
        for val in (-0.01, 1.01, -1.0, 2.0, 100):
            p = self.base_payload(top_p=val)
            with self.assertRaises(RuntimeErrorCode):
                validate_request(p)

    def test_invalid_top_p_types_rejected(self):
        for val in ("0.5", True, False, None, [], {}):
            p = self.base_payload(top_p=val)
            with self.assertRaises(RuntimeErrorCode):
                validate_request(p)


if __name__ == "__main__":
    unittest.main()
