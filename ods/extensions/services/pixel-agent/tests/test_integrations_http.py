"""Invented loopback HTTP and plaintext-output regressions; no external endpoints."""
import contextlib
import http.server
import json
import os
import stat
import tempfile
import pathlib
import sys
import threading
import time
import unittest
import urllib.request

if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires the Linux/WSL integrations host service")
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "host"))
import integrations_common as common  # noqa: E402
import integrations_mcp as mcp  # noqa: E402
from integrations_common import IntegrationError, redact_secrets, safe_urlopen  # noqa: E402


@contextlib.contextmanager
def listener(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.calls = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class Endpoint(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.calls.append(self.headers.get("Authorization"))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{}')

    def do_POST(self):
        self.server.calls.append(self.headers.get("Authorization"))
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.last_body_bytes = len(raw)
        if getattr(self.server, "redirect", None):
            self.send_response(self.server.redirect)
            self.send_header("Location", self.server.destination)
            self.end_headers()
            return
        message = json.loads(raw)
        if "id" not in message:
            self.send_response(202)
            self.end_headers()
            return
        if message["method"] == "initialize":
            reply = {"result": {}}
        elif message["method"] == "tools/list":
            self.server.pages = getattr(self.server, "pages", 0) + 1
            time.sleep(getattr(self.server, "page_delay", 0))
            mode = getattr(self.server, "catalog", "complete")
            count = 201 if mode == "over-limit" else 200 if mode in ("complete", "unfinished-limit") else 1
            tools = [{"name": f"tool_{self.server.pages}_{n}"} for n in range(count)]
            if mode == "malformed":
                tools = [{"name": "!not-a-tool"}]
            if mode == "identity-collision":
                token = self.server.token
                tools = [{"name": token, "description": "Description " + token,
                          "inputSchema": {"type": "object", "properties": {"mode": {
                              "type": "string", "enum": [token], "description": "Mode " + token}}}}]
            result = {"tools": tools}
            if mode in ("cycle", "many-pages", "unfinished-limit"):
                result["nextCursor"] = "repeat" if mode == "cycle" else str(self.server.pages)
            reply = {"result": result}
        elif getattr(self.server, "error", False):
            reply = {"error": {"code": -32000, "message": "x" * 100 + self.server.token}}
        else:
            reply = {"result": {"content": [{"type": "text", "text": "x" * 7995 + self.server.token + " tail"}]}}
        body = json.dumps({"jsonrpc": "2.0", "id": message["id"], **reply}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class HttpSecurityTests(unittest.TestCase):
    def test_redirects_never_forward_bearer_or_basic_credentials(self):
        with listener(Endpoint) as destination, listener(Endpoint) as origin:
            origin.destination = f"http://127.0.0.1:{destination.server_port}/other-origin?private=invented"
            for status in (301, 302, 303, 307, 308):
                for authorization in ("Bearer invented-not-real", "Basic aW52ZW50ZWQ6bm90LXJlYWw="):
                    with self.subTest(status=status, credential_kind=authorization.split()[0]):
                        origin.redirect = status
                        request = urllib.request.Request(f"http://127.0.0.1:{origin.server_port}/configured", data=b'{}',
                                                         headers={"Authorization": authorization}, method="POST")
                        with self.assertRaises(IntegrationError) as refused:
                            safe_urlopen(request, timeout=2)
                        self.assertEqual(refused.exception.code, "redirect-refused")
                        self.assertNotIn(origin.destination, refused.exception.message)
                        self.assertNotIn(authorization, refused.exception.message)
                        self.assertEqual(destination.calls, [])
            self.assertEqual(len(origin.calls), 10)

    def test_mcp_redirect_is_a_fixed_refusal_not_another_authenticated_request(self):
        with listener(Endpoint) as destination, listener(Endpoint) as origin:
            origin.redirect = 302
            origin.destination = f"http://127.0.0.1:{destination.server_port}/mcp"
            channel = mcp.HttpSession({"name": "Invented", "url": f"http://127.0.0.1:{origin.server_port}/mcp"}, "invented-MCP-token")
            with self.assertRaises(IntegrationError) as refused:
                channel.request("initialize", {})
            self.assertEqual(refused.exception.code, "redirect-refused")
            self.assertEqual(origin.calls, ["Bearer invented-MCP-token"])
            self.assertEqual(destination.calls, [])

    def test_http_tool_results_and_errors_redact_known_token_before_clipping(self):
        token = "INVENTED_TOKEN_BOUNDARY_1234567890"
        with listener(Endpoint) as endpoint:
            endpoint.token = token
            server = {"name": "Invented", "kind": "http", "url": f"http://127.0.0.1:{endpoint.server_port}/mcp"}
            with mcp.McpSession(server, token, {}, None) as channel:
                text, _ = channel.call("invented", {})
                self.assertNotIn(token[:5], text)
                endpoint.error = True
                with self.assertRaises(IntegrationError) as refused:
                    channel.call("invented", {})
                self.assertNotIn(token[:20], refused.exception.message)
                self.assertIn("[redacted]", refused.exception.message)

    def test_public_protocol_identities_are_not_rewritten_by_secret_hygiene(self):
        with listener(Endpoint) as endpoint:
            endpoint.catalog, endpoint.token = "identity-collision", "text"
            server = {"name": "Invented", "kind": "http", "url": f"http://127.0.0.1:{endpoint.server_port}/mcp"}
            with mcp.McpSession(server, endpoint.token, {}, None) as channel:
                tool, = channel.tools()
                self.assertEqual(tool["name"], "text")
                self.assertEqual(tool["inputSchema"]["type"], "object")
                self.assertEqual(tool["inputSchema"]["properties"]["mode"]["enum"], ["text"])
                self.assertEqual(tool["description"], "Description [redacted]")
                self.assertEqual(tool["inputSchema"]["properties"]["mode"]["description"], "Mode [redacted]")
                result, _ = channel.call("text", {})
                self.assertTrue(result.startswith("x" * 100), "the protocol content type 'text' must remain usable")
                self.assertNotIn("text", result)

    def test_valid_maximum_utf8_agent_input_is_not_rejected_by_private_wire_overhead(self):
        frame = {"schemaVersion": 1, "action": "mcp-call", "server": "http", "tool": "invented", "arguments": {"value": ""}}
        base = len(json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode())
        available = common.MAX_REQUEST_BYTES - 1 - base
        frame["arguments"]["value"] = "\U0001f642" * (available // 4) + "x" * (available % 4)
        self.assertEqual(len(json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode()) + 1, common.MAX_REQUEST_BYTES)
        with listener(Endpoint) as endpoint:
            endpoint.token = "invented-token"
            server = {"name": "Invented", "kind": "http", "url": f"http://127.0.0.1:{endpoint.server_port}/mcp"}
            channel = mcp.HttpSession(server, endpoint.token)
            result = channel.request("tools/call", {"name": "invented", "arguments": frame["arguments"]})
            self.assertIsInstance(result, dict)
            self.assertGreater(endpoint.last_body_bytes, common.MAX_REQUEST_BYTES)

    def test_short_secret_does_not_rename_structural_json_fields(self):
        self.assertEqual(redact_secrets({"id": "id", "name": "name"}, ["id", "name"]),
                         {"id": "[redacted]", "name": "[redacted]"})

    def test_dripping_headers_and_body_have_an_absolute_deadline_and_reap_the_worker(self):
        class Drip(Endpoint):
            def do_POST(self):
                message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.server.started.set()
                body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {}}).encode()
                headers = (f"HTTP/1.0 200 OK\r\nContent-Length: {len(body)}\r\nContent-Type: application/json\r\n\r\n").encode()
                try:
                    if self.server.mode == "headers":
                        for offset in range(0, len(headers), 4):
                            self.connection.sendall(headers[offset:offset + 4])
                            time.sleep(0.08)
                        self.connection.sendall(body)
                    else:
                        self.connection.sendall(headers)
                        for offset in range(0, len(body), 2):
                            self.connection.sendall(body[offset:offset + 2])
                            time.sleep(0.08)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        for mode in ("headers", "body"):
            with self.subTest(mode=mode), listener(Drip) as endpoint:
                endpoint.mode, endpoint.started = mode, threading.Event()
                workers = []
                spawn = common.subprocess.Popen
                def remember(*args, **kwargs):
                    worker = spawn(*args, **kwargs)
                    workers.append(worker)
                    self.assertNotIn("invented-private-token", repr((args, kwargs.get("env"))))
                    return worker
                request = urllib.request.Request(f"http://127.0.0.1:{endpoint.server_port}",
                    data=b'{"jsonrpc":"2.0","id":1,"method":"initialize"}',
                    headers={"Authorization": "Bearer invented-private-token"})
                started = time.monotonic()
                with patch.object(common.subprocess, "Popen", side_effect=remember):
                    with self.assertRaises(IntegrationError) as refused:
                        safe_urlopen(request, timeout=0.5)
                self.assertEqual(refused.exception.code, "integration-timeout")
                self.assertLess(time.monotonic() - started, 1.2)
                self.assertTrue(endpoint.started.is_set())
                self.assertEqual(len(workers), 1)
                self.assertIsNotNone(workers[0].poll(), "no terminal timeout receipt before the owned worker is reaped")

    def test_catalog_limits_do_not_publish_a_prefix_as_a_completed_check(self):
        for mode in ("over-limit", "unfinished-limit", "cycle", "many-pages", "malformed"):
            with self.subTest(mode=mode), listener(Endpoint) as endpoint:
                endpoint.catalog = mode
                server = {"name": "Invented", "kind": "http", "url": f"http://127.0.0.1:{endpoint.server_port}/mcp"}
                with mcp.McpSession(server, "invented-token", {}, None) as channel:
                    with self.assertRaises(IntegrationError) as refused:
                        channel.tools()
                self.assertIn(refused.exception.code, ("mcp-tool-limit", "mcp-protocol"))
                self.assertLessEqual(endpoint.pages, 10)
        with listener(Endpoint) as endpoint:
            endpoint.catalog = "complete"
            server = {"name": "Invented", "kind": "http", "url": f"http://127.0.0.1:{endpoint.server_port}/mcp"}
            with mcp.McpSession(server, "invented-token", {}, None) as channel:
                self.assertEqual(len(channel.tools()), 200)

    def test_pagination_uses_one_shared_session_budget(self):
        with listener(Endpoint) as endpoint:
            endpoint.catalog, endpoint.page_delay = "many-pages", 0.1
            server = {"name": "Invented", "kind": "http", "url": f"http://127.0.0.1:{endpoint.server_port}/mcp"}
            started = time.monotonic()
            with self.assertRaises(IntegrationError) as refused:
                with mcp.McpSession(server, "invented-token", {}, None, timeout=0.5) as channel:
                    channel.tools()
            self.assertIn(refused.exception.code, ("integration-timeout", "mcp-timeout"))
            self.assertLess(time.monotonic() - started, 1.2)
            self.assertLess(endpoint.pages, 10)

    def test_state_rename_fsyncs_file_then_directory(self):
        with tempfile.TemporaryDirectory() as root:
            store = common.Store(pathlib.Path(root) / "state")
            original = os.fsync
            ordered = []
            def observe(descriptor):
                ordered.append(stat.S_ISDIR(os.fstat(descriptor).st_mode))
                return original(descriptor)
            with patch.object(common.os, "fsync", side_effect=observe):
                store.save_approvals([])
            self.assertEqual(ordered, [False, True])

    def test_directory_sync_failure_is_not_reported_as_a_committed_approval(self):
        with tempfile.TemporaryDirectory() as root:
            store = common.Store(pathlib.Path(root) / "state")
            original = os.fsync
            def fail_directory(descriptor):
                if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    raise OSError("invented directory fsync fault")
                return original(descriptor)
            with patch.object(common.os, "fsync", side_effect=fail_directory):
                with self.assertRaises(OSError):
                    store.save_approvals([])

    def test_literal_redaction_copies_json_fields_and_multiline_components(self):
        original = {"payload": [{"message": "invented-long-token short\nline-one\nline-two"}], "count": 2}
        result = redact_secrets(original, {"token": "invented-long-token", "env": {"OTHER": "short", "MULTI": "line-one\nline-two"}})
        self.assertIn("invented-long-token", original["payload"][0]["message"])
        self.assertNotIn("invented-long-token", json.dumps(result))
        self.assertNotIn("short", json.dumps(result))
        self.assertNotIn("line-one", json.dumps(result))
        self.assertNotIn("line-two", json.dumps(result))
        self.assertEqual(result["count"], 2)


if __name__ == "__main__":
    unittest.main()
