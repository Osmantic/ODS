"""Unit test suite for single-level nested list flattening resilience."""

import unittest


def flatten_nested_list_shallow(nested_items: list | None) -> list:
    if not nested_items or not isinstance(nested_items, list):
        return []
    res = []
    for item in nested_items:
        if isinstance(item, (list, tuple, set)):
            res.extend(list(item))
        elif item is not None:
            res.append(item)
    return res


class TestListFlattenShallowResilience(unittest.TestCase):
    def test_flatten_shallow_valid(self):
        self.assertEqual(flatten_nested_list_shallow([[1, 2], [3, 4], 5]), [1, 2, 3, 4, 5])

    def test_flatten_shallow_none(self):
        self.assertEqual(flatten_nested_list_shallow(None), [])


if __name__ == "__main__":
    unittest.main()
