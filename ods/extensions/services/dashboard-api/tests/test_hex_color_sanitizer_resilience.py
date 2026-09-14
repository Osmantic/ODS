"""Unit test suite for CSS hex color string validation resilience."""

import re
import unittest

_HEX_COLOR_RE = re.compile(r"^#([A-Fa-f0-9]{3}|[A-Fa-f0-9]{6})$")


def sanitize_hex_color_string(color_str: str | None, default: str = "#4F46E5") -> str:
    if not color_str or not isinstance(color_str, str):
        return default
    cleaned = color_str.strip()
    if not cleaned.startswith("#"):
        cleaned = "#" + cleaned
    return cleaned if _HEX_COLOR_RE.match(cleaned) else default


class TestHexColorSanitizerResilience(unittest.TestCase):
    def test_sanitize_hex_color_valid(self):
        self.assertEqual(sanitize_hex_color_string("#3b82f6"), "#3b82f6")
        self.assertEqual(sanitize_hex_color_string("fff"), "#fff")

    def test_sanitize_hex_color_invalid(self):
        self.assertEqual(sanitize_hex_color_string("invalid_color"), "#4F46E5")

    def test_sanitize_hex_color_none(self):
        self.assertEqual(sanitize_hex_color_string(None), "#4F46E5")


if __name__ == "__main__":
    unittest.main()
