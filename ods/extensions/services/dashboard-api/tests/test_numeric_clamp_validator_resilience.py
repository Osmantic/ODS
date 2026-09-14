"""Unit test suite for generic numeric range clamping resilience."""

import unittest


def clamp_numeric_value_safely(val: object, min_val: float, max_val: float, default: float) -> float:
    if val is None:
        return float(default)
    try:
        n = float(val)
        return max(float(min_val), min(float(max_val), n))
    except (TypeError, ValueError):
        return float(default)


class TestNumericClampValidatorResilience(unittest.TestCase):
    def test_clamp_numeric_valid(self):
        self.assertEqual(clamp_numeric_value_safely(50, 0, 100, 10), 50.0)

    def test_clamp_numeric_out_of_range(self):
        self.assertEqual(clamp_numeric_value_safely(150, 0, 100, 10), 100.0)
        self.assertEqual(clamp_numeric_value_safely(-10, 0, 100, 10), 0.0)

    def test_clamp_numeric_none(self):
        self.assertEqual(clamp_numeric_value_safely(None, 0, 100, 10), 10.0)


if __name__ == "__main__":
    unittest.main()
