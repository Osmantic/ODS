#!/usr/bin/env python3
"""Regression test: runtime gateway validates user parameter string type and length."""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from pixel_provider.runtime_gateway import MODEL, RuntimeErrorCode, validate_request


class TestPixelGatewayUserParam(unittest.TestCase):
    def base_payload(self, **kwargs):
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": "hello"}],
        }
        payload.update(kwargs)
        return payload

    def test_valid_user_param_accepted(self):
        for val in ("user-123", "alice@example.com", "a" * 512):
            p = self.base_payload(user=val)
            res = validate_request(p)
            self.assertEqual(res["user"], val)

    def test_invalid_user_param_rejected(self):
        for val in ("", "a" * 513, 123, True, False, None, [], {}):
            p = self.base_payload(user=val)
            with self.assertRaises(RuntimeErrorCode):
                validate_request(p)


if __name__ == "__main__":
    unittest.main()
