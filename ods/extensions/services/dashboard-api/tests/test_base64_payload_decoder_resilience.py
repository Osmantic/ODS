"""Unit test suite for Base64 payload decoding resilience."""

import base64
import unittest


def decode_base64_payload_bytes(b64_str: str | None) -> bytes | None:
    if not b64_str or not isinstance(b64_str, str):
        return None
    cleaned = b64_str.strip()
    try:
        return base64.b64decode(cleaned, validate=True)
    except Exception:
        return None


class TestBase64PayloadDecoderResilience(unittest.TestCase):
    def test_decode_base64_valid(self):
        encoded = base64.b64encode(b"hello world").decode("utf-8")
        self.assertEqual(decode_base64_payload_bytes(encoded), b"hello world")

    def test_decode_base64_invalid(self):
        self.assertIsNone(decode_base64_payload_bytes("invalid_b64!!!"))
        self.assertIsNone(decode_base64_payload_bytes(None))


if __name__ == "__main__":
    unittest.main()
