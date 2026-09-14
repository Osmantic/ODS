"""Unit test suite for HTTP URL scheme and netloc validation resilience."""

import unittest
from urllib.parse import urlparse


def validate_http_url_scheme(url_str: str | None) -> tuple[bool, str]:
    if not url_str or not isinstance(url_str, str):
        return False, "invalid_type"
    try:
        parsed = urlparse(url_str.strip())
        if parsed.scheme not in ("http", "https"):
            return False, "unsupported_scheme"
        if not parsed.netloc:
            return False, "missing_host"
        return True, "valid"
    except Exception:
        return False, "malformed_url"


class TestHTTPURLValidatorResilience(unittest.TestCase):
    def test_validate_url_valid(self):
        ok, reason = validate_http_url_scheme("http://localhost:8000/health")
        self.assertTrue(ok)
        self.assertEqual(reason, "valid")

    def test_validate_url_invalid_scheme(self):
        ok, reason = validate_http_url_scheme("ftp://example.com")
        self.assertFalse(ok)
        self.assertEqual(reason, "unsupported_scheme")


if __name__ == "__main__":
    unittest.main()
