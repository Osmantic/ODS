"""Unit test suite for exponential backoff delay calculation resilience."""

import unittest


def calculate_exponential_backoff_seconds(attempt: int, base_sec: float = 1.0, max_sec: float = 60.0, factor: float = 2.0) -> float:
    if not isinstance(attempt, int) or attempt < 0:
        attempt = 0
    base = max(0.1, float(base_sec))
    mult = float(factor) ** min(attempt, 10)
    delay = base * mult
    return min(delay, float(max_sec))


class TestRetryBackoffCalculatorResilience(unittest.TestCase):
    def test_calculate_backoff_valid(self):
        self.assertEqual(calculate_exponential_backoff_seconds(0, base_sec=1.0), 1.0)
        self.assertEqual(calculate_exponential_backoff_seconds(2, base_sec=1.0), 4.0)

    def test_calculate_backoff_capped(self):
        self.assertEqual(calculate_exponential_backoff_seconds(10, base_sec=1.0, max_sec=30.0), 30.0)

    def test_calculate_backoff_negative(self):
        self.assertEqual(calculate_exponential_backoff_seconds(-5, base_sec=1.0), 1.0)


if __name__ == "__main__":
    unittest.main()
