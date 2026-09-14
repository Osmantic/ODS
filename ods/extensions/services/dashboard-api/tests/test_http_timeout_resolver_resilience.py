"""Unit test suite for HTTP request timeout value resolution resilience."""

import unittest


def resolve_http_timeout_seconds(val: object, default: float = 5.0, max_val: float = 30.0) -> float:
    if val is None:
        return float(default)
    try:
        num = float(val)
        if num <= 0:
            return float(default)
        return float(min(num, max_val))
    except (TypeError, ValueError):
        return float(default)


class TestHTTPTimeoutResolverResilience(unittest.TestCase):
    def test_resolve_timeout_valid(self):
        self.assertEqual(resolve_http_timeout_seconds("10.0"), 10.0)

    def test_resolve_timeout_clamped(self):
        self.assertEqual(resolve_http_timeout_seconds(60.0, max_val=30.0), 30.0)

    def test_resolve_timeout_negative(self):
        self.assertEqual(resolve_http_timeout_seconds(-1.0, default=5.0), 5.0)


if __name__ == "__main__":
    unittest.main()
