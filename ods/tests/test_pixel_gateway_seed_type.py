#!/usr/bin/env python3
"""Regression test: runtime gateway validates integer type for seed and rejects booleans/floats."""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from pixel_provider.runtime_gateway import MODEL, RuntimeErrorCode, validate_request


class TestPixelGatewaySeedType(unittest.TestCase):
    def base_payload(self, **kwargs):
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": "hello"}],
        }
        payload.update(kwargs)
        return payload

    def test_valid_integer_seed_accepted(self):
        for val in (0, 42, -100, 123456789, 2**31 - 1):
            p = self.base_payload(seed=val)
            res = validate_request(p)
            self.assertEqual(res["seed"], val)

    def test_invalid_seed_types_rejected(self):
        for val in (True, False, 1.0, "42", None, [], {}):
            p = self.base_payload(seed=val)
            with self.assertRaises(RuntimeErrorCode):
                validate_request(p)


if __name__ == "__main__":
    unittest.main()
