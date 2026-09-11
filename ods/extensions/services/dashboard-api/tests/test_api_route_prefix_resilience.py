"""Unit test suite for API route prefix normalization resilience."""

import unittest


def normalize_api_route_prefix(prefix_str: str | None) -> str:
    if not prefix_str or not isinstance(prefix_str, str):
        return "/api/v1"
    cleaned = prefix_str.strip()
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    cleaned = cleaned.rstrip("/")
    return cleaned if cleaned else "/api/v1"


class TestAPIRoutePrefixResilience(unittest.TestCase):
    def test_normalize_route_prefix_valid(self):
        self.assertEqual(normalize_api_route_prefix("api/v2/"), "/api/v2")

    def test_normalize_route_prefix_empty(self):
        self.assertEqual(normalize_api_route_prefix(""), "/api/v1")

    def test_normalize_route_prefix_none(self):
        self.assertEqual(normalize_api_route_prefix(None), "/api/v1")


if __name__ == "__main__":
    unittest.main()
