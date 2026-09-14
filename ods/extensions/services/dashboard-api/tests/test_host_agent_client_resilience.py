"""Unit test suite for host agent HTTP client resilience."""

import unittest
from host_agent_client import AgentClientError, AgentHTTPError


class TestHostAgentClientResilience(unittest.TestCase):
    def test_agent_client_error_inheritance(self):
        err = AgentClientError("connection error")
        self.assertIsInstance(err, RuntimeError)

    def test_agent_http_error_attributes(self):
        err = AgentHTTPError(status_code=500, detail="Internal Error", response_text="server crash")
        self.assertEqual(err.status_code, 500)
        self.assertEqual(err.detail, "Internal Error")
        self.assertEqual(err.response_text, "server crash")


if __name__ == "__main__":
    unittest.main()
