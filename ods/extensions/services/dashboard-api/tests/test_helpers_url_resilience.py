"""Unit test suite for dashboard helper URL sanitization and dir cache resilience."""

import unittest
from pathlib import Path
from helpers import _DirSizeCache


class TestHelpersUrlResilience(unittest.TestCase):
    def test_dir_size_cache_get_set(self):
        cache = _DirSizeCache(ttl=60.0)
        p = Path("/tmp")
        cache.set(p, 1.5)
        self.assertEqual(cache.get(p), 1.5)

    def test_dir_size_cache_miss(self):
        cache = _DirSizeCache(ttl=60.0)
        p = Path("/tmp/nonexistent_dir_for_test_cache")
        self.assertIsNone(cache.get(p))


if __name__ == "__main__":
    unittest.main()
