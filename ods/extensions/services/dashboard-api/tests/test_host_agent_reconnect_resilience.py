"""Unit test suite for host agent transport reconnect exponential backoff resilience."""

import unittest


def calculate_reconnect_backoff(attempt: int, max_backoff: float = 30.0) -> float:
    if not isinstance(attempt, int) or attempt < 1:
        return 1.0
    backoff = min(1.0 * (2 ** (attempt - 1)), max_backoff)
    return float(backoff)


class TestHostAgentReconnectResilience(unittest.TestCase):
    def test_calculate_reconnect_backoff_first_attempt(self):
        self.assertEqual(calculate_reconnect_backoff(1), 1.0)

    def test_calculate_reconnect_backoff_capped_max(self):
        self.assertEqual(calculate_reconnect_backoff(10, max_backoff=30.0), 30.0)

    def test_calculate_reconnect_backoff_invalid_attempt(self):
        self.assertEqual(calculate_reconnect_backoff(0), 1.0)


if __name__ == "__main__":
    unittest.main()
