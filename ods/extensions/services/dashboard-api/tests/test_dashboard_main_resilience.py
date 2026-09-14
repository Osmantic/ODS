"""Unit test suite for dashboard API application entrypoint resilience."""

import unittest
from main import app


class TestDashboardMainResilience(unittest.TestCase):
    def test_fastapi_app_initialization(self):
        self.assertIsNotNone(app)
        self.assertEqual(app.title, "ODS Dashboard API")


if __name__ == "__main__":
    unittest.main()
