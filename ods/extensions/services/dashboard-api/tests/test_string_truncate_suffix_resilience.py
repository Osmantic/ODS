"""Unit test suite for string truncation with ellipsis suffix resilience."""

import unittest


def truncate_string_with_suffix(text: str | None, max_length: int = 50, suffix: str = "...") -> str:
    if not text or not isinstance(text, str):
        return ""
    max_len = max(1, int(max_length))
    suf = str(suffix) if suffix is not None else ""
    if len(text) <= max_len:
        return text
    if max_len <= len(suf):
        return text[:max_len]
    return text[: max_len - len(suf)] + suf


class TestStringTruncateSuffixResilience(unittest.TestCase):
    def test_truncate_string_valid(self):
        self.assertEqual(truncate_string_with_suffix("hello world python", max_length=11), "hello wo...")

    def test_truncate_string_none(self):
        self.assertEqual(truncate_string_with_suffix(None), "")


if __name__ == "__main__":
    unittest.main()
