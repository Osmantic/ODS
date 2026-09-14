"""Unit test suite for substring occurrence counting resilience."""

import unittest


def count_substring_occurrences(text_str: str | None, sub_str: str | None) -> int:
    if not text_str or not isinstance(text_str, str):
        return 0
    if not sub_str or not isinstance(sub_str, str):
        return 0
    return text_str.count(sub_str)


class TestStringCountSubstringResilience(unittest.TestCase):
    def test_count_substring_valid(self):
        self.assertEqual(count_substring_occurrences("banana", "an"), 2)

    def test_count_substring_none(self):
        self.assertEqual(count_substring_occurrences(None, "a"), 0)


if __name__ == "__main__":
    unittest.main()
