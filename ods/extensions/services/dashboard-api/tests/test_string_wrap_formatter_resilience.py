"""Unit test suite for text line wrapping resilience."""

import textwrap
import unittest


def wrap_text_to_width(text_str: str | None, width: int = 80) -> list[str]:
    if not text_str or not isinstance(text_str, str):
        return []
    target_width = max(1, int(width))
    return textwrap.wrap(text_str.strip(), width=target_width)


class TestStringWrapFormatterResilience(unittest.TestCase):
    def test_wrap_text_valid(self):
        lines = wrap_text_to_width("hello world python programming", width=12)
        self.assertTrue(len(lines) > 1)
        self.assertTrue(all(len(line) <= 12 for line in lines))

    def test_wrap_text_none(self):
        self.assertEqual(wrap_text_to_width(None), [])


if __name__ == "__main__":
    unittest.main()
