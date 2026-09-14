"""Unit test suite for list partition grouping resilience."""

import unittest


def partition_list_by_predicate(items: list | None, predicate_fn: callable) -> tuple[list, list]:
    if not items or not isinstance(items, list):
        return [], []
    if not callable(predicate_fn):
        return list(items), []
    matching = []
    non_matching = []
    for item in items:
        try:
            if predicate_fn(item):
                matching.append(item)
            else:
                non_matching.append(item)
        except Exception:
            non_matching.append(item)
    return matching, non_matching


class TestListChunkByPredicateResilience(unittest.TestCase):
    def test_partition_list_valid(self):
        m, n = partition_list_by_predicate([1, 2, 3, 4, 5], lambda x: x % 2 == 0)
        self.assertEqual(m, [2, 4])
        self.assertEqual(n, [1, 3, 5])

    def test_partition_list_none(self):
        self.assertEqual(partition_list_by_predicate(None, lambda x: True), ([], []))


if __name__ == "__main__":
    unittest.main()
