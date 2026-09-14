"""Unit test suite for overlapping window list chunking resilience."""

import unittest


def chunk_list_with_overlap(items: list | None, chunk_size: int = 3, overlap: int = 1) -> list[list]:
    if not items or not isinstance(items, list):
        return []
    sz = max(1, int(chunk_size))
    ov = max(0, min(sz - 1, int(overlap)))
    step = sz - ov
    res = []
    for i in range(0, len(items), step):
        sub = items[i : i + sz]
        if sub:
            res.append(sub)
    return res


class TestListChunkOverlappingResilience(unittest.TestCase):
    def test_chunk_list_overlap_valid(self):
        res = chunk_list_with_overlap([1, 2, 3, 4, 5], chunk_size=3, overlap=1)
        self.assertEqual(res, [[1, 2, 3], [3, 4, 5], [5]])

    def test_chunk_list_overlap_none(self):
        self.assertEqual(chunk_list_with_overlap(None), [])


if __name__ == "__main__":
    unittest.main()
