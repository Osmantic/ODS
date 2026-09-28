#!/usr/bin/env python3
"""Regression test: runtime gateway validates stop parameter type and max sequence count."""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from pixel_provider.runtime_gateway import MODEL, RuntimeErrorCode, validate_request


class TestPixelGatewayStopParam(unittest.TestCase):
    def base_payload(self, **kwargs):
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": "hello"}],
        }
        payload.update(kwargs)
        return payload

    def test_valid_stop_accepted(self):
        for val in ("\n", "STOP", ["\n"], ["stop1", "stop2", "stop3", "stop4"], []):
            p = self.base_payload(stop=val)
            res = validate_request(p)
            self.assertEqual(res["stop"], val)

    def test_invalid_stop_too_many_sequences_rejected(self):
        p = self.base_payload(stop=["1", "2", "3", "4", "5"])
        with self.assertRaises(RuntimeErrorCode):
            validate_request(p)

    def test_invalid_stop_types_rejected(self):
        for val in (123, True, False, None, {}, ["stop", 123], [None]):
            p = self.base_payload(stop=val)
            with self.assertRaises(RuntimeErrorCode):
                validate_request(p)


if __name__ == "__main__":
    unittest.main()
