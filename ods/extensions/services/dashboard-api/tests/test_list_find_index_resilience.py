"""Unit test suite for safe item index search resilience in lists."""

import unittest


def find_item_index_safely(items: list | None, target: object) -> int:
    if not items or not isinstance(items, list):
        return -1
    try:
        return items.index(target)
    except ValueError:
        return -1


class TestListFindIndexResilience(unittest.TestCase):
    def test_find_index_valid(self):
        self.assertEqual(find_item_index_safely([10, 20, 30], 20), 1)
        self.assertEqual(find_item_index_safely([10, 20, 30], 99), -1)

    def test_find_index_none(self):
        self.assertEqual(find_item_index_safely(None, 10), -1)


if __name__ == "__main__":
    unittest.main()
