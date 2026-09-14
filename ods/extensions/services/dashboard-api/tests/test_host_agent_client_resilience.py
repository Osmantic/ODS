"""Tests for host_agent_client transport resilience and retry logic."""

import errno
import unittest
from unittest.mock import MagicMock

from host_agent_client import (
    AgentHTTPError,
    _error_detail,
    _is_transient_route_connect_error,
)


class TestHostAgentClientResilience(unittest.TestCase):
    def test_transient_route_connect_error_by_errno(self):
        exc = OSError(errno.ENETUNREACH, "Network unreachable")
        self.assertTrue(_is_transient_route_connect_error(exc))

        exc_host = OSError(errno.EHOSTUNREACH, "No route to host")
        self.assertTrue(_is_transient_route_connect_error(exc_host))

    def test_transient_route_connect_error_by_message(self):
        exc = Exception("Error: network is unreachable while setting up route")
        self.assertTrue(_is_transient_route_connect_error(exc))

    def test_non_transient_connect_error(self):
        exc = OSError(errno.ECONNREFUSED, "Connection refused")
        self.assertFalse(_is_transient_route_connect_error(exc))

    def test_error_detail_extraction_from_json(self):
        mock_resp = MagicMock()
        mock_resp.text = '{"error": "Unauthorized access"}'
        mock_resp.json.return_value = {"error": "Unauthorized access"}
        mock_resp.status_code = 401

        detail, text = _error_detail(mock_resp)
        self.assertEqual(detail, "Unauthorized access")
        self.assertEqual(text, '{"error": "Unauthorized access"}')

    def test_error_detail_fallback_for_non_json(self):
        mock_resp = MagicMock()
        mock_resp.text = "Internal Server Error"
        mock_resp.json.side_effect = ValueError("Invalid JSON")
        mock_resp.status_code = 500

        detail, text = _error_detail(mock_resp)
        self.assertEqual(detail, "Host agent returned HTTP 500")
        self.assertEqual(text, "Internal Server Error")

    def test_agent_http_error_properties(self):
        err = AgentHTTPError(status_code=403, detail="Forbidden", response_text="Permission denied")
        self.assertEqual(err.status_code, 403)
        self.assertEqual(err.detail, "Forbidden")
        self.assertEqual(err.response_text, "Permission denied")


if __name__ == "__main__":
    unittest.main()
