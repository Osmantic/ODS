"""Real subprocess/HTTP checks for the Docker Desktop edge control transport."""
import http.server
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "bin"))
import pixel_access_bridge as bridge


@unittest.skipUnless(sys.platform == "linux", "The adapter and exec child run on Linux/WSL")
class EdgeContainerTransportTests(unittest.TestCase):
    def setUp(self):
        self.response = b'{"phase":"idle"}'
        self.mode = "normal"
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                outer.requests.append((self.command, self.path, self.headers.get("Authorization"), body))
                self.send_response(200)
                self.send_header("Connection", "close")
                if outer.mode != "close":
                    self.send_header("Content-Length", str(len(outer.response) + (7 if outer.mode == "short" else 0)))
                self.end_headers()
                try:
                    if outer.mode == "drip":
                        for byte in outer.response:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(0.08)
                    else:
                        self.wfile.write(outer.response)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True

            do_POST = do_GET

            def log_message(self, *_):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.real_popen = subprocess.Popen
        self.children = []

        def launch(args, **kwargs):
            self.assertEqual(args[:6], ["docker", "exec", "-i", "a" * 64, "python3", "-I"])
            self.assertNotIn("k" * 64, " ".join(args))
            self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
            script = args[7].replace('"127.0.0.1", 9595', '"127.0.0.1", ' + str(self.server.server_port))
            process = self.real_popen([sys.executable, "-I", "-c", script, args[8]], **kwargs)
            self.children.append(process)
            return process

        self.launch = patch.object(bridge.subprocess, "Popen", side_effect=launch)
        self.launch.start()

    def tearDown(self):
        self.launch.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        for child in self.children:
            self.assertIsNotNone(child.poll(), "CLI must be reaped or terminal")

    def request(self, path="/v1/transition", payload=None, timeout=2):
        return bridge._edge_container_request("a" * 64, path, "k" * 64, payload, timeout)

    def test_get_connection_close_and_complete_json(self):
        for mode in ("normal", "close"):
            with self.subTest(mode=mode):
                self.mode = mode
                self.assertEqual(self.request(), {"phase": "idle"})
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[0][:3], ("GET", "/v1/transition", "Bearer " + "k" * 64))

    def test_mutation_receives_exact_payload_once(self):
        payload = {"token": "b" * 64, "revision": "c" * 64}
        self.assertEqual(self.request("/v1/transition/acquire", payload), {"phase": "idle"})
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(json.loads(self.requests[0][3]), payload)

    def test_native_gateway_status_and_mutation_reach_loopback(self):
        value = {"available": True, "phase": "idle", "revision": "c" * 64, "active": 0}
        self.response = json.dumps(value).encode()
        adapter = bridge.SystemdAccessBridge("/unused", "k" * 64)
        adapter.native_origin = "http://127.0.0.1:" + str(self.server.server_port)
        adapter.native_key = "k" * 64
        self.assertEqual(adapter.native(), value)
        self.assertEqual(adapter.native("acquire", "b" * 64), value)
        self.assertEqual([r[0] for r in self.requests], ["GET", "GET", "POST"])
        self.assertTrue(all(r[1:3] == ("/pixel-ods/access-runtime", "Bearer " + "k" * 64)
                            for r in self.requests))
        self.assertEqual(json.loads(self.requests[-1][3]),
                         {"operation": "acquire", "token": "b" * 64, "revision": "c" * 64})
        self.assertEqual(self.children, [])

    def test_rejects_short_http_frame_even_if_body_is_valid_json(self):
        self.mode = "short"
        with self.assertRaises(bridge.AccessError): self.request()

    def test_maximum_complete_response_is_not_truncated(self):
        self.response = b'{"x":"' + b'a' * (65536 - 8) + b'"}'
        self.assertEqual(len(self.response), 65536)
        self.assertEqual(len(self.request()["x"]), 65536 - 8)

    def test_child_alarm_expires_even_when_stdin_never_closes(self):
        child = self.real_popen([sys.executable, "-I", "-c", bridge._EDGE_CONTAINER_SCRIPT, "0.15"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.children.append(child)
        try:
            self.assertEqual(child.wait(timeout=1.5), 124)
            self.assertEqual(child.stdout.read(), b"")
            self.assertEqual(child.stderr.read(), b"")
        finally:
            for stream in (child.stdin, child.stdout, child.stderr): stream.close()

    def test_rejects_oversize_duplicate_nonfinite_and_nonobject_json(self):
        for body in (b"x" * 65537, b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'{}{}'):
            with self.subTest(body=body[:30]):
                self.response = body
                with self.assertRaises(bridge.AccessError): self.request()

    def test_slow_drip_is_bounded_without_retry(self):
        self.mode = "drip"
        start = time.monotonic()
        with self.assertRaisesRegex(bridge.AccessError, "runtime-operation-timeout"):
            self.request(timeout=0.25)
        self.assertLess(time.monotonic() - start, 1.5)
        self.assertEqual(len(self.requests), 1)

    def test_invalid_operation_and_credentials_never_launch(self):
        cases = [("/v1/transition/restart", "k" * 64, None),
                 ("/v1/transition", "k" * 32 + "\n", None),
                 ("/v1/transition/acquire", "k" * 64, {"token": "bad", "revision": "c" * 64})]
        for path, key, payload in cases:
            with self.assertRaises(bridge.AccessError):
                bridge._edge_container_request("a" * 64, path, key, payload)
        self.assertEqual(self.children, [])

    def test_edge_pins_running_id_instead_of_private_ip(self):
        adapter = bridge.SystemdAccessBridge("/unused", "k" * 64)
        with patch.object(adapter, "command", return_value="a" * 64 + " true") as inspect:
            self.assertEqual(adapter.edge(), {"phase": "idle"})
        self.assertEqual(inspect.call_args.args[0][1:3], ["inspect", "ods-pixel-edge"])
        with patch.object(adapter, "command", return_value="a" * 64 + " false"):
            with self.assertRaisesRegex(bridge.AccessError, "edge-container-unavailable"):
                adapter.edge()


if __name__ == "__main__":
    unittest.main()
