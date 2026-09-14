"""Unit test suite for JSON request payload decoder resilience."""

import json
import unittest


def safe_decode_json_payload(raw_body: str | bytes | None, default_dict: dict | None = None) -> dict:
    if default_dict is None:
        default_dict = {}
    if not raw_body:
        return default_dict
    try:
        data = json.loads(raw_body)
        return data if isinstance(data, dict) else default_dict
    except Exception:
        return default_dict


class TestJsonPayloadDecoderResilience(unittest.TestCase):
    def test_safe_decode_json_valid(self):
        data = safe_decode_json_payload('{"status": "ok"}')
        self.assertEqual(data["status"], "ok")

    def test_safe_decode_json_invalid(self):
        self.assertEqual(safe_decode_json_payload("invalid_json"), {})
        self.assertEqual(safe_decode_json_payload(None), {})


if __name__ == "__main__":
    unittest.main()
