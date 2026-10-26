"""Connected-apps status through a real, disposable loopback host-agent HTTP server."""

from __future__ import annotations

import http.client
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import pixel_access_relay  # noqa: E402
from pixel_connections import catalog, status  # noqa: E402

KEY = "synthetic-connections-test-key"


class ConnectionsHostAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("_pixel_connections_test_agent", ROOT / "bin/ods-host-agent.py")
        cls.agent = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.agent
        spec.loader.exec_module(cls.agent)
        cls.agent.AGENT_API_KEY = KEY
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.agent.AgentHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        sys.modules.pop("_pixel_connections_test_agent", None)

    def request(self, *, token=KEY, path="/v1/pixel/connections"):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request("GET", path, headers={"Authorization": "Bearer " + token})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_the_owner_gets_the_status_with_the_shipped_catalog(self):
        reply = status.unavailable("darwin")
        with patch.object(pixel_access_relay, "request_runtime_connections", return_value=(200, reply)) as relay:
            code, body = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(body, dict(reply, catalog=catalog.public_catalog()))
        relay.assert_called_once()

    def test_owner_auth_comes_first(self):
        with patch.object(pixel_access_relay, "request_runtime_connections") as relay:
            self.assertEqual(self.request(token="wrong")[0], 403)
            self.assertEqual(self.request(path="/v1/pixel/connections?account=other")[0], 404)
        relay.assert_not_called()

    def test_an_unexpected_status_shape_is_never_forwarded(self):
        reply = dict(status.unavailable("darwin"), token="private-sentinel")
        with patch.object(pixel_access_relay, "request_runtime_connections", return_value=(200, reply)):
            code, body = self.request()
        self.assertEqual((code, body), (503, {"error": "connections-unavailable"}))

    def test_an_unreachable_runtime_is_a_plain_unavailable(self):
        failure = pixel_access_relay.AccessRelayError("agent-access-runtime-unavailable")
        with patch.object(pixel_access_relay, "request_runtime_connections", side_effect=failure):
            self.assertEqual(self.request(), (503, {"error": "connections-unavailable"}))

    def test_controller_refusals_pass_through_without_a_catalog(self):
        with patch.object(pixel_access_relay, "request_runtime_connections",
                          return_value=(400, {"error": "invalid-request"})):
            self.assertEqual(self.request(), (400, {"error": "invalid-request"}))


if __name__ == "__main__":
    unittest.main()
