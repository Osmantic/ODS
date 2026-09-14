"""Unit test suite for statistical mode element computation resilience in lists."""

import unittest
from collections import Counter


def compute_list_mode_element(items: list | None) -> object:
    if not items or not isinstance(items, list):
        return None
    try:
        counts = Counter(items)
        if not counts:
            return None
        return counts.most_common(1)[0][0]
    except TypeError:
        return None


class TestListModeElementResilience(unittest.TestCase):
    def test_compute_mode_valid(self):
        self.assertEqual(compute_list_mode_element([1, 2, 2, 3, 3, 3, 4]), 3)

    def test_compute_mode_none(self):
        self.assertEqual(compute_list_mode_element(None), None)


if __name__ == "__main__":
    unittest.main()
