"""Unit test suite for list slice pagination index bounds resilience."""

import unittest


def paginate_sequence_safely(items: list | None, offset: int = 0, limit: int = 10) -> tuple[list, int]:
    if not items or not isinstance(items, list):
        return [], 0
    total = len(items)
    safe_offset = max(0, offset)
    safe_limit = max(1, limit)
    slice_items = items[safe_offset : safe_offset + safe_limit]
    return slice_items, total


class TestListPaginationBoundsResilience(unittest.TestCase):
    def test_paginate_sequence_valid(self):
        items = list(range(20))
        page, total = paginate_sequence_safely(items, offset=5, limit=5)
        self.assertEqual(len(page), 5)
        self.assertEqual(page[0], 5)
        self.assertEqual(total, 20)

    def test_paginate_sequence_negative_offset(self):
        items = [1, 2, 3]
        page, total = paginate_sequence_safely(items, offset=-5, limit=2)
        self.assertEqual(len(page), 2)
        self.assertEqual(page[0], 1)

    def test_paginate_sequence_none(self):
        page, total = paginate_sequence_safely(None)
        self.assertEqual(page, [])
        self.assertEqual(total, 0)


if __name__ == "__main__":
    unittest.main()
