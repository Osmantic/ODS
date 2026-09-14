"""Unit test suite for token counter estimation resilience."""

import unittest


def estimate_token_count(text: str | None, avg_chars_per_token: float = 4.0) -> int:
    if not text or not isinstance(text, str):
        return 0
    if avg_chars_per_token <= 0:
        avg_chars_per_token = 4.0
    return max(1, int(len(text) / avg_chars_per_token)) if text.strip() else 0


class TestTokenCounterResilience(unittest.TestCase):
    def test_estimate_token_count_valid(self):
        self.assertEqual(estimate_token_count("Hello world! This is a test."), 7)

    def test_estimate_token_count_empty(self):
        self.assertEqual(estimate_token_count(""), 0)
        self.assertEqual(estimate_token_count(None), 0)


if __name__ == "__main__":
    unittest.main()
