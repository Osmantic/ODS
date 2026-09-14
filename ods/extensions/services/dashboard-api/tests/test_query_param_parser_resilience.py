"""Unit test suite for HTTP query parameter integer conversion resilience."""

import unittest


def parse_query_param_int(param_val: object, default: int = 0, min_val: int | None = None, max_val: int | None = None) -> int:
    if param_val is None:
        val = default
    else:
        try:
            val = int(param_val)
        except (TypeError, ValueError):
            val = default
    if min_val is not None:
        val = max(min_val, val)
    if max_val is not None:
        val = min(max_val, val)
    return val


class TestQueryParamParserResilience(unittest.TestCase):
    def test_parse_query_param_valid(self):
        self.assertEqual(parse_query_param_int("50", default=10, min_val=1, max_val=100), 50)

    def test_parse_query_param_invalid(self):
        self.assertEqual(parse_query_param_int("invalid", default=10), 10)

    def test_parse_query_param_clamped(self):
        self.assertEqual(parse_query_param_int("500", min_val=1, max_val=100), 100)


if __name__ == "__main__":
    unittest.main()
