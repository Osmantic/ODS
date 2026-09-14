"""Unit test suite for binary payload string encoding resilience."""

import unittest


def safe_encode_binary_payload(text: str | bytes | None, encoding: str = "utf-8") -> bytes:
    if text is None:
        return b""
    if isinstance(text, bytes):
        return text
    try:
        return str(text).encode(encoding, errors="replace")
    except Exception:
        return b""


class TestBinaryPayloadEncoderResilience(unittest.TestCase):
    def test_encode_binary_valid(self):
        self.assertEqual(safe_encode_binary_payload("hello world"), b"hello world")
        self.assertEqual(safe_encode_binary_payload(b"existing_bytes"), b"existing_bytes")

    def test_encode_binary_none(self):
        self.assertEqual(safe_encode_binary_payload(None), b"")


if __name__ == "__main__":
    unittest.main()
