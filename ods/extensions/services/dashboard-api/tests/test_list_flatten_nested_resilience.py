"""Unit test suite for 2D list flattening resilience."""

import unittest


def flatten_two_dimensional_list(nested_list: list[list] | None) -> list:
    if not nested_list or not isinstance(nested_list, list):
        return []
    res = []
    for sub in nested_list:
        if isinstance(sub, (list, tuple)):
            res.extend(sub)
        elif sub is not None:
            res.append(sub)
    return res


class TestListFlattenNestedResilience(unittest.TestCase):
    def test_flatten_list_valid(self):
        self.assertEqual(flatten_two_dimensional_list([[1, 2], [3, 4], 5]), [1, 2, 3, 4, 5])

    def test_flatten_list_none(self):
        self.assertEqual(flatten_two_dimensional_list(None), [])


if __name__ == "__main__":
    unittest.main()
