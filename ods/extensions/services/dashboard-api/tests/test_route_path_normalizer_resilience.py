"""Unit test suite for HTTP route path normalization resilience."""

import re
import unittest

_SLASH_DUP_RE = re.compile(r"/{2,}")


def normalize_http_route_path(path_str: str | None) -> str:
    if not path_str or not isinstance(path_str, str):
        return "/"
    cleaned = path_str.strip()
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    cleaned = _SLASH_DUP_RE.sub("/", cleaned)
    if len(cleaned) > 1 and cleaned.endswith("/"):
        cleaned = cleaned[:-1]
    return cleaned


class TestRoutePathNormalizerResilience(unittest.TestCase):
    def test_normalize_route_path_valid(self):
        self.assertEqual(normalize_http_route_path("//api///v1/models/"), "/api/v1/models")

    def test_normalize_route_path_root(self):
        self.assertEqual(normalize_http_route_path("/"), "/")

    def test_normalize_route_path_none(self):
        self.assertEqual(normalize_http_route_path(None), "/")


if __name__ == "__main__":
    unittest.main()
