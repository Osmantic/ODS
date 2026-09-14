"""Unit test suite for nested list flattener resilience."""

import unittest


def flatten_nested_list_safely(nested: list | None) -> list:
    if not nested or not isinstance(nested, list):
        return []
    flat = []
    for item in nested:
        if isinstance(item, list):
            flat.extend(flatten_nested_list_safely(item))
        elif item is not None:
            flat.append(item)
    return flat


class TestJSONArrayFlattenerResilience(unittest.TestCase):
    def test_flatten_nested_list_valid(self):
        self.assertEqual(flatten_nested_list_safely([1, [2, [3, 4]], 5]), [1, 2, 3, 4, 5])

    def test_flatten_nested_list_none(self):
        self.assertEqual(flatten_nested_list_safely(None), [])


if __name__ == "__main__":
    unittest.main()
