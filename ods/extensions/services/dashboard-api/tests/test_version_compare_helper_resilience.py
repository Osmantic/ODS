"""Unit test suite for version comparison resilience."""

import unittest


def is_version_at_least(current_ver: tuple[int, int, int], min_ver: tuple[int, int, int]) -> bool:
    if not isinstance(current_ver, tuple) or len(current_ver) < 3:
        return False
    if not isinstance(min_ver, tuple) or len(min_ver) < 3:
        return True
    return current_ver[:3] >= min_ver[:3]


class TestVersionCompareHelperResilience(unittest.TestCase):
    def test_version_at_least_true(self):
        self.assertTrue(is_version_at_least((1, 5, 0), (1, 2, 0)))
        self.assertTrue(is_version_at_least((2, 0, 0), (2, 0, 0)))

    def test_version_at_least_false(self):
        self.assertFalse(is_version_at_least((1, 1, 0), (1, 2, 0)))

    def test_version_at_least_invalid(self):
        self.assertFalse(is_version_at_least(None, (1, 0, 0)))


if __name__ == "__main__":
    unittest.main()
