"""Native access errors must fail closed without escaping as TypeError."""
import http.server
import json
import pathlib
import sys
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "bin"))
from pixel_access_bridge import AccessError, SystemdAccessBridge  # noqa: E402


class Unauthorized(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"error": {"type": "unauthorized", "message": "Unauthorized"}}).encode()
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class NativeHttpErrors(unittest.TestCase):
    def test_structured_unauthorized_error_fails_closed(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), Unauthorized)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            bridge = SystemdAccessBridge("/tmp/ods-test", "test")
            origin = "http://127.0.0.1:%d" % server.server_port
            with self.assertRaises(AccessError) as caught:
                bridge.http(origin, "/pixel-ods/access-runtime", "test-auth-key")
            self.assertEqual(caught.exception.code, "runtime-unavailable-or-busy")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
