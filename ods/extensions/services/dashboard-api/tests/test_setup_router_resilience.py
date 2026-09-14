"""Unit test suite for setup wizard router resilience."""

import unittest


class TestSetupRouterResilience(unittest.TestCase):
    def test_setup_router_import(self):
        from routers import setup
        self.assertIsNotNone(setup.router)


if __name__ == "__main__":
    unittest.main()
