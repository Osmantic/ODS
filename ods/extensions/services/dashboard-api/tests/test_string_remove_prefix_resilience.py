"""Unit test suite for prefix removal string formatting resilience."""

import unittest


def remove_string_prefix_safely(text: str | None, prefix: str | None) -> str:
    if not text or not isinstance(text, str):
        return ""
    if not prefix or not isinstance(prefix, str):
        return text
    if text.startswith(prefix):
        return text[len(prefix) :]
    return text


class TestStringRemovePrefixResilience(unittest.TestCase):
    def test_remove_prefix_valid(self):
        self.assertEqual(remove_string_prefix_safely("env_production", "env_"), "production")

    def test_remove_prefix_none(self):
        self.assertEqual(remove_string_prefix_safely(None, "prefix"), "")


if __name__ == "__main__":
    unittest.main()
