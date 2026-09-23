#!/usr/bin/env python3
"""Regression test: runtime gateway validates reasoning_effort parameter values."""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from pixel_provider.runtime_gateway import MODEL, RuntimeErrorCode, validate_request


class TestPixelGatewayReasoningEffort(unittest.TestCase):
    def base_payload(self, **kwargs):
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": "hello"}],
        }
        payload.update(kwargs)
        return payload

    def test_valid_reasoning_effort_accepted(self):
        for val in ("low", "medium", "high"):
            p = self.base_payload(reasoning_effort=val)
            res = validate_request(p)
            self.assertEqual(res["reasoning_effort"], val)

    def test_invalid_reasoning_effort_values_rejected(self):
        for val in ("none", "extreme", "max", "MIN", "", 123, True, False, None, []):
            p = self.base_payload(reasoning_effort=val)
            with self.assertRaises(RuntimeErrorCode):
                validate_request(p)


if __name__ == "__main__":
    unittest.main()
