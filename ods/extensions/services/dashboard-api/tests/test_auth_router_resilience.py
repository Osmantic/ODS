"""Unit test suite for auth router resilience."""

import unittest


class TestAuthRouterResilience(unittest.TestCase):
    def test_auth_router_import(self):
        from routers import auth
        self.assertIsNotNone(auth.router)


if __name__ == "__main__":
    unittest.main()
