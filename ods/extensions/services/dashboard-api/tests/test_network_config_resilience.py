"""Unit test suite for network configuration resolver resilience."""

import unittest


class TestNetworkConfigResilience(unittest.TestCase):
    def test_network_config_import(self):
        from routers import node
        self.assertIsNotNone(node)


if __name__ == "__main__":
    unittest.main()
