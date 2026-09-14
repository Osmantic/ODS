"""Unit test suite for string slugification formatting resilience."""

import re
import unittest

_SLUG_NON_ALPHANUM_RE = re.compile(r"[^\w\s-]")
_SLUG_HYPHEN_RE = re.compile(r"[-\s]+")


def slugify_string_safely(raw_str: str | None) -> str:
    if not raw_str or not isinstance(raw_str, str):
        return ""
    cleaned = raw_str.strip().lower()
    cleaned = _SLUG_NON_ALPHANUM_RE.sub("", cleaned)
    cleaned = _SLUG_HYPHEN_RE.sub("-", cleaned)
    return cleaned.strip("-")


class TestStringSlugifyFormatterResilience(unittest.TestCase):
    def test_slugify_string_valid(self):
        self.assertEqual(slugify_string_safely("  Hello World! Python-3.13  "), "hello-world-python-313")

    def test_slugify_string_none(self):
        self.assertEqual(slugify_string_safely(None), "")


if __name__ == "__main__":
    unittest.main()
