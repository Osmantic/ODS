"""Unit test suite for numeric list cumulative sum sequence resilience."""

import itertools
import unittest


def calculate_numeric_cumulative_sum(numbers: list[int | float] | None) -> list[float]:
    if not numbers or not isinstance(numbers, list):
        return []
    valid_nums = []
    for n in numbers:
        try:
            valid_nums.append(float(n))
        except (ValueError, TypeError):
            pass
    if not valid_nums:
        return []
    return list(itertools.accumulate(valid_nums))


class TestListCumulativeSumResilience(unittest.TestCase):
    def test_cumulative_sum_valid(self):
        res = calculate_numeric_cumulative_sum([1, 2, 3, 4])
        self.assertEqual(res, [1.0, 3.0, 6.0, 10.0])

    def test_cumulative_sum_none(self):
        self.assertEqual(calculate_numeric_cumulative_sum(None), [])


if __name__ == "__main__":
    unittest.main()
