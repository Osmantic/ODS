"""Unit test suite for SHA256 checksum digest verification resilience."""

import hashlib
import unittest


def verify_sha256_checksum(data_bytes: bytes | None, expected_hex: str | None) -> tuple[bool, str]:
    if not data_bytes or not isinstance(data_bytes, bytes):
        return False, "missing_bytes"
    if not expected_hex or not isinstance(expected_hex, str):
        return False, "missing_checksum"
    actual_hex = hashlib.sha256(data_bytes).hexdigest()
    if actual_hex.lower() != expected_hex.strip().lower():
        return False, "checksum_mismatch"
    return True, "valid"


class TestHashDigestVerifierResilience(unittest.TestCase):
    def test_verify_sha256_valid(self):
        data = b"hello world"
        expected = hashlib.sha256(data).hexdigest()
        ok, reason = verify_sha256_checksum(data, expected)
        self.assertTrue(ok)
        self.assertEqual(reason, "valid")

    def test_verify_sha256_mismatch(self):
        ok, reason = verify_sha256_checksum(b"hello world", "invalid_digest")
        self.assertFalse(ok)
        self.assertEqual(reason, "checksum_mismatch")


if __name__ == "__main__":
    unittest.main()
