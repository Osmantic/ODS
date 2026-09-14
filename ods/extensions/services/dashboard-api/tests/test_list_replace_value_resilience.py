"""Unit test suite for element substitution list replacement resilience."""

import unittest


def replace_list_occurrences(items: list | None, old_val: object, new_val: object) -> list:
    if not items or not isinstance(items, list):
        return []
    return [new_val if x == old_val else x for x in items]


class TestListReplaceValueResilience(unittest.TestCase):
    def test_replace_list_occurrences_valid(self):
        res = replace_list_occurrences([1, 2, 3, 2, 4], 2, 99)
        self.assertEqual(res, [1, 99, 3, 99, 4])

    def test_replace_list_occurrences_none(self):
        self.assertEqual(replace_list_occurrences(None, 1, 2), [])


if __name__ == "__main__":
    unittest.main()
