"""Unit test suite for text payload line counting resilience."""

import unittest


def count_text_payload_lines(text: str | None) -> int:
    if not text or not isinstance(text, str):
        return 0
    return len(text.splitlines())


class TestStringCountLinesResilience(unittest.TestCase):
    def test_count_lines_valid(self):
        self.assertEqual(count_text_payload_lines("line1\nline2\nline3"), 3)

    def test_count_lines_none(self):
        self.assertEqual(count_text_payload_lines(None), 0)


if __name__ == "__main__":
    unittest.main()
