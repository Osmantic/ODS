"""Unit test suite for CORS allowed origins LAN IP resolution resilience."""

import unittest
from main import get_allowed_origins


class TestAllowedOriginsResilience(unittest.TestCase):
    def test_get_allowed_origins_contains_localhost(self):
        origins = get_allowed_origins()
        self.assertIsInstance(origins, list)
        self.assertTrue(any("localhost" in o or "127.0.0.1" in o for o in origins))

    def test_get_allowed_origins_returns_nonempty_list(self):
        origins = get_allowed_origins()
        self.assertGreater(len(origins), 0)


if __name__ == "__main__":
    unittest.main()
