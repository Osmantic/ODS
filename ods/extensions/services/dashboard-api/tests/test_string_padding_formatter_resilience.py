"""Unit test suite for string padding and truncation resilience."""

import unittest


def pad_or_truncate_string(text: str | None, width: int = 20, pad_char: str = " ") -> str:
    if text is None:
        text = ""
    else:
        text = str(text)
    w = max(1, min(1000, width))
    p = pad_char[0] if pad_char else " "
    if len(text) > w:
        return text[:w]
    return text.ljust(w, p)


class TestStringPaddingFormatterResilience(unittest.TestCase):
    def test_pad_or_truncate_valid(self):
        self.assertEqual(pad_or_truncate_string("hello", width=10, pad_char="-"), "hello-----")

    def test_pad_or_truncate_too_long(self):
        self.assertEqual(pad_or_truncate_string("very long text string", width=9), "very long")

    def test_pad_or_truncate_none(self):
        self.assertEqual(pad_or_truncate_string(None, width=5, pad_char="0"), "00000")


if __name__ == "__main__":
    unittest.main()
