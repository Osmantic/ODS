"""Unit test suite for order-preserving list deduplication resilience."""

import unittest


def deduplicate_list_preserve_order(items: list | None) -> list:
    if not items or not isinstance(items, list):
        return []
    seen = set()
    res = []
    for item in items:
        try:
            if item not in seen:
                seen.add(item)
                res.append(item)
        except TypeError:
            if item not in res:
                res.append(item)
    return res


class TestListDeduplicateOrderResilience(unittest.TestCase):
    def test_deduplicate_preserve_order_valid(self):
        res = deduplicate_list_preserve_order([3, 1, 2, 3, 1, 4])
        self.assertEqual(res, [3, 1, 2, 4])

    def test_deduplicate_preserve_order_none(self):
        self.assertEqual(deduplicate_list_preserve_order(None), [])


if __name__ == "__main__":
    unittest.main()
