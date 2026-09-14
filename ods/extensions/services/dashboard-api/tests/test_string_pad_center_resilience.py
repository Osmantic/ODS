"""Unit test suite for centered string padding formatting resilience."""

import unittest


def pad_string_centered(text: str | None, width: int = 40, fill_char: str = " ") -> str:
    if not text or not isinstance(text, str):
        return ""
    w = max(1, int(width))
    fill = str(fill_char)[0] if fill_char else " "
    return text.center(w, fill)


class TestStringPadCenterResilience(unittest.TestCase):
    def test_pad_string_centered_valid(self):
        self.assertEqual(pad_string_centered("header", width=10, fill_char="-"), "--header--")

    def test_pad_string_centered_none(self):
        self.assertEqual(pad_string_centered(None), "")


if __name__ == "__main__":
    unittest.main()
