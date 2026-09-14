"""Unit test suite for suffix removal string formatting resilience."""

import unittest


def remove_string_suffix_safely(text: str | None, suffix: str | None) -> str:
    if not text or not isinstance(text, str):
        return ""
    if not suffix or not isinstance(suffix, str):
        return text
    if text.endswith(suffix):
        return text[: -len(suffix)]
    return text


class TestStringRemoveSuffixResilience(unittest.TestCase):
    def test_remove_suffix_valid(self):
        self.assertEqual(remove_string_suffix_safely("database_v1.0", ".0"), "database_v1")

    def test_remove_suffix_none(self):
        self.assertEqual(remove_string_suffix_safely(None, "suffix"), "")


if __name__ == "__main__":
    unittest.main()
