"""Unit test suite for sliding window list batching resilience."""

import unittest


def create_sliding_window_batches(items: list | None, window_size: int = 3, step: int = 1) -> list[list]:
    if not items or not isinstance(items, list):
        return []
    wsz = max(1, int(window_size))
    stp = max(1, int(step))
    res = []
    for i in range(0, len(items), stp):
        win = items[i : i + wsz]
        if win:
            res.append(win)
    return res


class TestListBatchSlidingResilience(unittest.TestCase):
    def test_sliding_window_valid(self):
        batches = create_sliding_window_batches([1, 2, 3, 4], window_size=2, step=1)
        self.assertEqual(batches, [[1, 2], [2, 3], [3, 4], [4]])

    def test_sliding_window_none(self):
        self.assertEqual(create_sliding_window_batches(None), [])


if __name__ == "__main__":
    unittest.main()
