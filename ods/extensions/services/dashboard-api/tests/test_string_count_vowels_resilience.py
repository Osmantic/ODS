"""Unit test suite for vowel character counting string formatting resilience."""

import unittest


def count_string_vowels_safely(text: str | None) -> int:
    if not text or not isinstance(text, str):
        return 0
    vowels = set("aeiouAEIOU")
    return sum(1 for char in text if char in vowels)


class TestStringCountVowelsResilience(unittest.TestCase):
    def test_count_vowels_valid(self):
        self.assertEqual(count_string_vowels_safely("hello world"), 3)

    def test_count_vowels_none(self):
        self.assertEqual(count_string_vowels_safely(None), 0)


if __name__ == "__main__":
    unittest.main()
