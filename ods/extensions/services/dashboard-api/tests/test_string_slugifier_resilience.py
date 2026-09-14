"""Unit test suite for URL string slugification resilience."""

import re
import unittest

_SLUG_NON_ALPHANUM_RE = re.compile(r"[^\w\s-]")
_SLUG_HYPHEN_RE = re.compile(r"[-\s]+")


def slugify_string_safely(text: str | None, default: str = "default-slug") -> str:
    if not text or not isinstance(text, str):
        return default
    cleaned = _SLUG_NON_ALPHANUM_RE.sub("", text.strip().lower())
    slug = _SLUG_HYPHEN_RE.sub("-", cleaned).strip("-")
    return slug if slug else default


class TestStringSlugifierResilience(unittest.TestCase):
    def test_slugify_string_valid(self):
        self.assertEqual(slugify_string_safely("My Custom Model 2.0"), "my-custom-model-20")

    def test_slugify_string_special_chars(self):
        self.assertEqual(slugify_string_safely("!!! $$$ %%%"), "default-slug")

    def test_slugify_string_none(self):
        self.assertEqual(slugify_string_safely(None), "default-slug")


if __name__ == "__main__":
    unittest.main()
