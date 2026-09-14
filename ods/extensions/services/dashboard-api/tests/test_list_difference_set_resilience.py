"""Unit test suite for set difference list comparison resilience."""

import unittest


def compute_list_set_difference(list_a: list | None, list_b: list | None) -> list:
    if not list_a or not isinstance(list_a, list):
        return []
    if not list_b or not isinstance(list_b, list):
        return list(list_a)
    try:
        set_b = set(list_b)
        return [x for x in list_a if x not in set_b]
    except TypeError:
        return [x for x in list_a if x not in list_b]


class TestListDifferenceSetResilience(unittest.TestCase):
    def test_set_difference_valid(self):
        res = compute_list_set_difference([1, 2, 3, 4], [2, 4])
        self.assertEqual(res, [1, 3])

    def test_set_difference_none(self):
        self.assertEqual(compute_list_set_difference(None, [1]), [])


if __name__ == "__main__":
    unittest.main()
