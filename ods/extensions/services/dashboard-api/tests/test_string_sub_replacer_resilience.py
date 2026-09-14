"""Unit test suite for safe string substring replacement resilience."""

import unittest


def replace_substring_safely(text_str: str | None, target: str | None, replacement: str | None) -> str:
    if not text_str or not isinstance(text_str, str):
        return ""
    if not target or not isinstance(target, str):
        return text_str
    repl = str(replacement) if replacement is not None else ""
    return text_str.replace(target, repl)


class TestStringSubReplacerResilience(unittest.TestCase):
    def test_replace_substring_valid(self):
        res = replace_substring_safely("hello world", "world", "there")
        self.assertEqual(res, "hello there")

    def test_replace_substring_none_target(self):
        self.assertEqual(replace_substring_safely("hello world", None, "there"), "hello world")

    def test_replace_substring_none_text(self):
        self.assertEqual(replace_substring_safely(None, "a", "b"), "")


if __name__ == "__main__":
    unittest.main()
