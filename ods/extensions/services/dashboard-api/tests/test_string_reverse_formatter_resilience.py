"""Unit test suite for string reverse slicing resilience."""

import unittest


def reverse_string_characters(val: str | None) -> str:
    if val is None or not isinstance(val, str):
        return ""
    return val[::-1]


class TestStringReverseFormatterResilience(unittest.TestCase):
    def test_reverse_string_valid(self):
        self.assertEqual(reverse_string_characters("abcde"), "edcba")

    def test_reverse_string_none(self):
        self.assertEqual(reverse_string_characters(None), "")


if __name__ == "__main__":
    unittest.main()
