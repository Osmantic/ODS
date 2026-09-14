"""Unit test suite for numeric range step generation resilience."""

import unittest


def generate_bounded_number_range(start: int, stop: int, step: int = 1, max_items: int = 100) -> list[int]:
    try:
        st = int(start)
        sp = int(stop)
        step_val = int(step)
        if step_val == 0:
            step_val = 1
        cap = max(1, int(max_items))
        res = list(range(st, sp, step_val))
        return res[:cap]
    except (TypeError, ValueError):
        return []


class TestNumberRangeStepResilience(unittest.TestCase):
    def test_generate_range_valid(self):
        self.assertEqual(generate_bounded_number_range(0, 10, 2), [0, 2, 4, 6, 8])

    def test_generate_range_zero_step(self):
        self.assertEqual(generate_bounded_number_range(0, 3, 0), [0, 1, 2])

    def test_generate_range_invalid(self):
        self.assertEqual(generate_bounded_number_range(None, 10), [])


if __name__ == "__main__":
    unittest.main()
