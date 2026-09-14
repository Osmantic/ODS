"""Unit test suite for URL query parameter stripping resilience."""

import unittest
from urllib.parse import urlparse, urlunparse


def strip_url_query_parameters(url_str: str | None) -> str | None:
    if not url_str or not isinstance(url_str, str):
        return None
    try:
        parsed = urlparse(url_str.strip())
        clean_parsed = parsed._replace(query="", fragment="")
        return urlunparse(clean_parsed)
    except Exception:
        return None


class TestURLQueryStripperResilience(unittest.TestCase):
    def test_strip_query_valid(self):
        self.assertEqual(
            strip_url_query_parameters("https://example.com/api?token=secret#section"),
            "https://example.com/api",
        )

    def test_strip_query_none(self):
        self.assertIsNone(strip_url_query_parameters(None))


if __name__ == "__main__":
    unittest.main()
