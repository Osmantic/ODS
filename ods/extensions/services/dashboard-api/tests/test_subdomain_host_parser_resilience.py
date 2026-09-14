"""Unit test suite for subdomain host header parsing resilience."""

import unittest


def extract_subdomain_prefix(host_header: str | None) -> str | None:
    if not host_header or not isinstance(host_header, str):
        return None
    cleaned = host_header.strip().split(":")[0].lower()
    parts = cleaned.split(".")
    if len(parts) >= 3:
        return parts[0]
    return None


class TestSubdomainHostParserResilience(unittest.TestCase):
    def test_extract_subdomain_valid(self):
        self.assertEqual(extract_subdomain_prefix("api.dashboard.example.com:8000"), "api")

    def test_extract_subdomain_none_or_tld(self):
        self.assertIsNone(extract_subdomain_prefix("example.com"))
        self.assertIsNone(extract_subdomain_prefix(None))


if __name__ == "__main__":
    unittest.main()
