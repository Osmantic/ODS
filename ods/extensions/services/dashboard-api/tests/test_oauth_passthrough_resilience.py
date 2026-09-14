"""Unit test suite for OAuth passthrough router resilience."""

import unittest


class TestOAuthPassthroughResilience(unittest.TestCase):
    def test_oauth_passthrough_router_import(self):
        from routers import oauth_passthrough
        self.assertIsNotNone(oauth_passthrough.router)


if __name__ == "__main__":
    unittest.main()
