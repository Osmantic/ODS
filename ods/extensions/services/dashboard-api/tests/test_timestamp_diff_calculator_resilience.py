"""Unit test suite for timestamp delta duration calculation resilience."""

import unittest
from datetime import datetime


def calculate_timestamp_diff_seconds(start_dt: datetime | None, end_dt: datetime | None) -> float:
    if not isinstance(start_dt, datetime) or not isinstance(end_dt, datetime):
        return 0.0
    diff = (end_dt - start_dt).total_seconds()
    return max(0.0, float(diff))


class TestTimestampDiffCalculatorResilience(unittest.TestCase):
    def test_calculate_diff_valid(self):
        t1 = datetime(2026, 8, 30, 12, 0, 0)
        t2 = datetime(2026, 8, 30, 12, 0, 10)
        self.assertEqual(calculate_timestamp_diff_seconds(t1, t2), 10.0)

    def test_calculate_diff_negative(self):
        t1 = datetime(2026, 8, 30, 12, 0, 10)
        t2 = datetime(2026, 8, 30, 12, 0, 0)
        self.assertEqual(calculate_timestamp_diff_seconds(t1, t2), 0.0)

    def test_calculate_diff_none(self):
        self.assertEqual(calculate_timestamp_diff_seconds(None, None), 0.0)


if __name__ == "__main__":
    unittest.main()
