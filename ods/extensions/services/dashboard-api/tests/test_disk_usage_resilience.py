"""Unit test suite for disk usage byte formatting resilience."""

import unittest


def format_bytes_to_gb(size_bytes: int | float) -> float:
    if not isinstance(size_bytes, (int, float)) or size_bytes < 0:
        return 0.0
    return round(float(size_bytes) / (1024 ** 3), 2)


class TestDiskUsageResilience(unittest.TestCase):
    def test_format_bytes_to_gb_valid(self):
        gb = format_bytes_to_gb(10737418240)
        self.assertEqual(gb, 10.0)

    def test_format_bytes_to_gb_negative(self):
        self.assertEqual(format_bytes_to_gb(-100), 0.0)

    def test_format_bytes_to_gb_invalid_type(self):
        self.assertEqual(format_bytes_to_gb("invalid"), 0.0)


if __name__ == "__main__":
    unittest.main()
