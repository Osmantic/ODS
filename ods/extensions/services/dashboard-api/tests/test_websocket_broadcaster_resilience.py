"""Unit test suite for WebSocket broadcasting connection manager resilience."""

import unittest


class MockWebSocket:
    def __init__(self, is_alive: bool = True):
        self.is_alive = is_alive

    async def send_json(self, data: dict):
        if not self.is_alive:
            raise RuntimeError("Connection closed")


def filter_active_connections(connections: list[MockWebSocket]) -> list[MockWebSocket]:
    if not isinstance(connections, list):
        return []
    return [ws for ws in connections if getattr(ws, "is_alive", False)]


class TestWebSocketBroadcasterResilience(unittest.TestCase):
    def test_filter_active_connections_valid(self):
        active_ws = MockWebSocket(True)
        dead_ws = MockWebSocket(False)
        res = filter_active_connections([active_ws, dead_ws])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0], active_ws)

    def test_filter_active_connections_none(self):
        self.assertEqual(filter_active_connections(None), [])


if __name__ == "__main__":
    unittest.main()
