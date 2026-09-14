"""Unit test suite for SHA1 payload checksum verification resilience."""

import hashlib
import unittest


def calculate_sha1_hex_digest(data: bytes | None) -> str | None:
    if not data or not isinstance(data, bytes):
        return None
    try:
        return hashlib.sha1(data).hexdigest()
    except Exception:
        return None


class TestFileChecksumSHA1Resilience(unittest.TestCase):
    def test_calculate_sha1_valid(self):
        digest = calculate_sha1_hex_digest(b"hello world")
        self.assertIsNotNone(digest)
        self.assertEqual(len(digest), 40)

    def test_calculate_sha1_none(self):
        self.assertIsNone(calculate_sha1_hex_digest(None))


if __name__ == "__main__":
    unittest.main()
