"""Unit test suite for string whitespace stripping resilience."""

import unittest


def strip_string_whitespace(val: str | None) -> str:
    if val is None or not isinstance(val, str):
        return ""
    return val.strip()


class TestStringWhitespaceTrimmerResilience(unittest.TestCase):
    def test_strip_whitespace_valid(self):
        self.assertEqual(strip_string_whitespace("   hello world   "), "hello world")

    def test_strip_whitespace_none(self):
        self.assertEqual(strip_string_whitespace(None), "")


if __name__ == "__main__":
    unittest.main()
