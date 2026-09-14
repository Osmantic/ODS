"""Unit test suite for system metrics percentage sampling resilience."""

import unittest


def clamp_percentage_metric(value: int | float | None) -> float:
    if value is None or not isinstance(value, (int, float)):
        return 0.0
    return float(max(0.0, min(100.0, float(value))))


class TestSystemMetricsResilience(unittest.TestCase):
    def test_clamp_percentage_valid(self):
        self.assertEqual(clamp_percentage_metric(45.5), 45.5)

    def test_clamp_percentage_overflow(self):
        self.assertEqual(clamp_percentage_metric(120.0), 100.0)

    def test_clamp_percentage_underflow(self):
        self.assertEqual(clamp_percentage_metric(-10.0), 0.0)

    def test_clamp_percentage_none(self):
        self.assertEqual(clamp_percentage_metric(None), 0.0)


if __name__ == "__main__":
    unittest.main()
