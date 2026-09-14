"""Unit test suite for list batch chunking resilience."""

import unittest


def chunk_list_into_batches(items: list | None, chunk_size: int = 10) -> list[list]:
    if not items or not isinstance(items, list):
        return []
    sz = max(1, int(chunk_size))
    return [items[i : i + sz] for i in range(0, len(items), sz)]


class TestListChunkSplitterResilience(unittest.TestCase):
    def test_chunk_list_valid(self):
        batches = chunk_list_into_batches([1, 2, 3, 4, 5], chunk_size=2)
        self.assertEqual(len(batches), 3)
        self.assertEqual(batches[0], [1, 2])
        self.assertEqual(batches[2], [5])

    def test_chunk_list_invalid_size(self):
        batches = chunk_list_into_batches([1, 2, 3], chunk_size=-5)
        self.assertEqual(len(batches), 3)

    def test_chunk_list_none(self):
        self.assertEqual(chunk_list_into_batches(None), [])


if __name__ == "__main__":
    unittest.main()
