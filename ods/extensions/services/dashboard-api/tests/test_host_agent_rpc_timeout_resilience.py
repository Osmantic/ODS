"""Unit test suite for host agent RPC request timeout bounds resilience."""

import unittest


def resolve_rpc_timeout(requested_timeout: float | int | None, default_timeout: float = 10.0, max_timeout: float = 60.0) -> float:
    if requested_timeout is None or not isinstance(requested_timeout, (int, float)):
        return float(default_timeout)
    if requested_timeout <= 0:
        return float(default_timeout)
    return float(min(float(requested_timeout), max_timeout))


class TestHostAgentRPCTimeoutResilience(unittest.TestCase):
    def test_resolve_rpc_timeout_valid(self):
        self.assertEqual(resolve_rpc_timeout(15.0), 15.0)

    def test_resolve_rpc_timeout_capped(self):
        self.assertEqual(resolve_rpc_timeout(120.0, max_timeout=60.0), 60.0)

    def test_resolve_rpc_timeout_negative(self):
        self.assertEqual(resolve_rpc_timeout(-5.0, default_timeout=10.0), 10.0)


if __name__ == "__main__":
    unittest.main()
