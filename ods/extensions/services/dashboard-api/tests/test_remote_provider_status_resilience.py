"""Unit test suite for remote provider status probe resilience."""

import unittest


class TestRemoteProviderStatusResilience(unittest.TestCase):
    def test_remote_provider_status_import(self):
        from routers import remote_provider_status
        self.assertIsNotNone(remote_provider_status)


if __name__ == "__main__":
    unittest.main()
