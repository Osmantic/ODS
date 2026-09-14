"""Unit test suite for features toggle router resilience."""

import unittest


class TestFeaturesToggleResilience(unittest.TestCase):
    def test_features_router_import(self):
        from routers import features
        self.assertIsNotNone(features.router)


if __name__ == "__main__":
    unittest.main()
