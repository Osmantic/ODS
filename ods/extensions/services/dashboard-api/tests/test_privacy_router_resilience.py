"""Unit test suite for privacy router resilience."""

import unittest


class TestPrivacyRouterResilience(unittest.TestCase):
    def test_privacy_router_import(self):
        from routers import privacy
        self.assertIsNotNone(privacy.router)


if __name__ == "__main__":
    unittest.main()
