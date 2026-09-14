"""Unit test suite for word capitalization string formatting resilience."""

import unittest


def capitalize_string_words_safely(text: str | None) -> str:
    if not text or not isinstance(text, str):
        return ""
    return " ".join(word.capitalize() for word in text.split())


class TestStringCapitalizeWordsResilience(unittest.TestCase):
    def test_capitalize_words_valid(self):
        self.assertEqual(capitalize_string_words_safely("hello world from python"), "Hello World From Python")

    def test_capitalize_words_none(self):
        self.assertEqual(capitalize_string_words_safely(None), "")


if __name__ == "__main__":
    unittest.main()
