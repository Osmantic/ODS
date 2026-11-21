"""Behavioral tests for pixel_adapter against a real ThreadingHTTPServer fixture."""
from __future__ import annotations

import json
import os
import sys
import subprocess
import tempfile
from pathlib import Path
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pixel_adapter as pa


class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.stream_body = b""
        self.stream_status = 200
        self.stream_ct = "text/event-stream"
        self.stream_delay = 0.0
        self.stream_trickle = None  # list of (delay, bytes)
        self.header_trickle = False
        self.cancel_delay = 0
        self.shutdown = threading.Event()
        self.result_queue = []  # list of (status, ct, body, delay)
        self.cancel_body = b'{"aborted":true}'
        self.cancel_status = 200
        self.activity_body = b'{"state":"terminal"}'
        self.activity_status = 200
        self.requests = []  # (endpoint, body_dict)
        self.redirect = None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: _State = None

    def log_message(self, *a):
        pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _read_body(self):
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def _send(self, status, ct, body, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        st = self.state
        body = self._read_body()
        endpoint = self.path.rsplit("/", 1)[-1]
        if self.path != "/api/pixel/chat/" + endpoint:
            self._send(404, "text/plain", b"wrong path")
            return
        with st.lock:
            st.requests.append((endpoint, body))
        if st.redirect and endpoint == "stream":
            self.send_response(st.redirect)
            self.send_header("Location", "/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if endpoint == "stream":
            if st.header_trickle:
                self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Delay: ")
                self.wfile.flush()
                while not st.shutdown.wait(0.1):
                    self.wfile.write(b"a")
                    self.wfile.flush()
                return
            if st.stream_trickle:
                self.send_response(st.stream_status)
                self.send_header("Content-Type", st.stream_ct)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for delay, chunk in st.stream_trickle:
                    if st.shutdown.wait(delay):
                        return
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                return
            if st.stream_delay:
                time.sleep(st.stream_delay)
            self._send(st.stream_status, st.stream_ct, st.stream_body)
            return
        if endpoint == "result":
            with st.lock:
                item = st.result_queue.pop(0) if st.result_queue else (200, "application/json", b'{"state":"unknown","events":""}', 0.0)
            status, ct, body, delay = item
            if delay:
                time.sleep(delay)
            self._send(status, ct, body)
            return
        if endpoint == "cancel":
            if st.cancel_delay:
                st.shutdown.wait(st.cancel_delay)
            self._send(st.cancel_status, "application/json", st.cancel_body)
            return
        if endpoint == "activity":
            self._send(st.activity_status, "application/json", st.activity_body)
            return
        self._send(404, "text/plain", b"no")


class _Server:
    def __init__(self):
        self.state = _State()
        handler = type("H", (_Handler,), {"state": self.state})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def close(self):
        self.state.shutdown.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


VALIDATOR = {"kind": "exact", "expected": "42"}


def _manifest(base, **over):
    m = {
        "schema_version": 1,
        "base_url": base,
        "api_key_env": "PIXEL_KEY",
        "timeout_s": 5,
        "recovery_timeout_s": 5,
        "task": {"id": "t1", "messages": [{"role": "user", "content": "q"}], "validator": VALIDATOR},
    }
    m.update(over)
    return m


def _sse(*frames):
    out = []
    for f in frames:
        out.append("data: " + (f if isinstance(f, str) else json.dumps(f)) + "\n\n")
    return "".join(out).encode()


def _delta(content=None, tool=None):
    d = {}
    if content is not None:
        d["content"] = content
    if tool is not None:
        d["tool_calls"] = [{"function": {"name": tool}}]
    return {"choices": [{"index": 0, "delta": d}]}


def _result_body(state, events):
    return json.dumps({"state": state, "events": events}).encode()


class Base(unittest.TestCase):
    def setUp(self):
        self.srv = _Server()
        self.env = patch.dict(os.environ, {"PIXEL_KEY": "sekret"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.srv.close()

    def run_manifest(self, **over):
        return pa.run(_manifest(self.srv.base, **over))


class TestRetainedCanonical(Base):
    def test_correct_answer_with_unknown_activity_is_not_success(self):
        st = self.srv.state
        st.stream_body = _sse(_delta("42"), "[DONE]")
        st.result_queue = [(200, "application/json", _result_body("complete", st.stream_body.decode()), 0)]
        st.activity_body = b'{"state":"unknown"}'
        r = self.run_manifest()
        self.assertTrue(r["answer_quality_pass"])
        self.assertFalse(r["success"])
        self.assertFalse(r["admission_safe"])

    def test_stream_wrong_retained_right(self):
        st = self.srv.state
        st.stream_body = _sse(_delta("wrong"), "[DONE]")
        st.result_queue = [(200, "application/json", _result_body("complete", _sse(_delta("42", tool="search"), "[DONE]").decode()), 0.0)]
        r = self.run_manifest()
        self.assertTrue(r["answer_quality_pass"])
        self.assertTrue(r["quality_pass"])
        self.assertTrue(r["success"])
        self.assertFalse(r["censored"])
        self.assertEqual(r["result_state"], "complete")
        self.assertEqual(r["http_status"], 200)
        self.assertTrue(r["submission_attempted"])
        self.assertTrue(r["transport_complete"])
        self.assertEqual(r["activity"], "terminal")
        self.assertTrue(r["admission_safe"])
        self.assertFalse(r["cancel_attempted"])
        # exactly one stream submission
        streams = [x for x in st.requests if x[0] == "stream"]
        self.assertEqual(len(streams), 1)
        # exact identity echoed
        self.assertEqual(set(streams[0][1]), {"chat_id", "request_id", "messages"})
        self.assertEqual(streams[0][1]["chat_id"], r["chat_id"])
        self.assertEqual(streams[0][1]["request_id"], r["request_id"])


class TestRequiredTool(Base):
    def test_required_tool_blocks_success(self):
        st = self.srv.state
        st.stream_body = _sse(_delta(tool="search"), "[DONE]")
        st.result_queue = [(200, "application/json", _result_body("complete", _sse(_delta("42", tool="search"), "[DONE]").decode()), 0.0)]
        r = self.run_manifest(task={"id": "t1", "messages": [{"role": "user", "content": "q"}], "validator": VALIDATOR, "required_tools": ["search"]})
        self.assertTrue(r["answer_quality_pass"])
        self.assertFalse(r["quality_pass"])
        self.assertFalse(r["success"])
        self.assertEqual(r["required_tools"], ["search"])
        self.assertEqual(r["requested_tools"], ["search"])
        self.assertIsNone(r["tool_execution_proven"])
        self.assertIsNone(r["owned_cleanup_proven"])


class TestMalformedRetained(Base):
    def _run_with_events(self, events):
        st = self.srv.state
        st.stream_body = _sse(_delta("42"), "[DONE]")
        st.result_queue = [(200, "application/json", _result_body("complete", events), 0.0)]
        return self.run_manifest()

    def test_duplicate_json(self):
        r = self._run_with_events('data: {"choices":[],"choices":[]}\n\ndata: [DONE]\n\n')
        self.assertFalse(r["answer_quality_pass"])
        self.assertFalse(r["success"])

    def test_empty_done(self):
        r = self._run_with_events("data: [DONE]\n\n")
        self.assertFalse(r["answer_quality_pass"])
        self.assertFalse(r["success"])

    def test_error_after_done(self):
        r = self._run_with_events('data: {"choices":[{"index":0,"delta":{"content":"42"}}]}\n\ndata: [DONE]\n\ndata: {"error":"x"}\n\n')
        self.assertFalse(r["answer_quality_pass"])
        self.assertFalse(r["success"])


class TestDisconnectCancel(Base):
    def test_disconnect_unknown_cancel(self):
        st = self.srv.state
        st.stream_body = b""  # empty -> invalid_sse
        st.result_queue = [
            (200, "application/json", b'{"state":"unknown","events":""}', 0.0),
            (200, "application/json", b'{"state":"cancelled","events":""}', 0.0),
        ]
        st.activity_body = b'{"state":"unknown"}'
        r = self.run_manifest()
        self.assertTrue(r["cancel_attempted"])
        self.assertTrue(r["cancel_ack"])
        self.assertEqual(r["result_state"], "cancelled")
        self.assertEqual(r["activity"], "unknown")
        self.assertFalse(r["admission_safe"])
        self.assertFalse(r["success"])
        streams = [x for x in st.requests if x[0] == "stream"]
        cancels = [x for x in st.requests if x[0] == "cancel"]
        self.assertEqual(len(streams), 1)
        self.assertEqual(len(cancels), 1)
        self.assertEqual(set(cancels[0][1]), {"chat_id", "request_id"})
        self.assertEqual(cancels[0][1], {key: r[key] for key in ("chat_id", "request_id")})
        self.assertIsNone(r["owned_cleanup_proven"])


class TestLateComplete(Base):
    def test_late_complete_censored(self):
        st = self.srv.state
        st.stream_body = _sse(_delta("42"), "[DONE]")
        # Slow first read exceeds the task deadline; only the post-cancel read
        # can observe the final answer. No second generation is submitted.
        st.result_queue = [
            (200, "application/json", b'{"state":"active","events":""}', 1.3),
            (200, "application/json", _result_body("complete", _sse(_delta("42"), "[DONE]").decode()), 0.0),
        ]
        r = self.run_manifest(timeout_s=1, recovery_timeout_s=1)
        self.assertTrue(r["censored"])
        self.assertTrue(r["answer_quality_pass"])
        self.assertFalse(r["success"])
        self.assertLess(r["elapsed_ms"], 3000)

    def test_recovery_budget_bounded(self):
        st = self.srv.state
        st.stream_body = b""
        st.cancel_delay = 2
        st.result_queue = [(200, "application/json", b'{"state":"active","events":""}', 0.0)] * 50
        st.activity_body = b'{"state":"unknown"}'
        r = self.run_manifest(timeout_s=1, recovery_timeout_s=1)
        self.assertLess(r["recovery_elapsed_ms"], 2500)
        self.assertLess(r["elapsed_ms"], 3500)


class TestHttpErrors(Base):
    def test_redirect_not_success(self):
        self.srv.state.redirect = 302
        r = self.run_manifest()
        self.assertFalse(r["success"])
        self.assertEqual(r["http_status"], 302)

    def test_409_not_success(self):
        self.srv.state.stream_status = 409
        self.srv.state.stream_body = b"conflict"
        r = self.run_manifest()
        self.assertFalse(r["success"])
        self.assertEqual(r["http_status"], 409)
        self.assertEqual(r["stream_error"], "http_rejected")


class TestRedaction(Base):
    def test_no_secrets_in_receipt(self):
        st = self.srv.state
        st.stream_body = _sse(_delta("42"), "[DONE]")
        st.result_queue = [(200, "application/json", _result_body("complete", _sse(_delta("42"), "[DONE]").decode()), 0.0)]
        r = self.run_manifest()
        blob = json.dumps(r)
        self.assertNotIn("PIXEL_KEY", blob)
        self.assertNotIn("sekret", blob)
        self.assertNotIn('"content"', blob)
        self.assertIn("answer_sha256", r)
        self.assertIsNotNone(r["answer_sha256"])

    def test_errors_sanitized(self):
        self.srv.state.stream_body = b"garbage"
        self.srv.state.result_queue = [(200, "application/json", b'{"state":"unknown","events":""}', 0.0)]
        r = self.run_manifest()
        allowed = {None, "timeout", "transport_error", "invalid_sse", "invalid_result", "invalid_cancel", "http_rejected", "bad_content_type", "body_too_large"}
        self.assertIn(r["stream_error"], allowed)
        self.assertIn(r["result_error"], allowed)


class TestInvalidManifest(Base):
    def test_invalid_url(self):
        with self.assertRaises(ValueError):
            pa.run(_manifest("not a url"))

    def test_nonfinite_deadline(self):
        with self.assertRaises(ValueError):
            pa.run(_manifest(self.srv.base, timeout_s=float("inf")))

    def test_bool_deadline(self):
        with self.assertRaises(ValueError):
            pa.run(_manifest(self.srv.base, timeout_s=True))

    def test_bad_ids(self):
        with self.assertRaises(ValueError):
            pa.run(_manifest(self.srv.base), chat_id="bad id!")
        with self.assertRaises(ValueError):
            pa.run(_manifest(self.srv.base), request_id="")

    def test_nonfinite_expected_value(self):
        manifest = _manifest(self.srv.base)
        manifest["task"]["validator"] = {"kind": "json_equal", "expected": float("nan")}
        with self.assertRaises(ValueError):
            pa.run(manifest)

    def test_duplicate_answer_keys_fail_quality(self):
        st = self.srv.state
        events = _sse(_delta('{"a":1,"a":1}'), "[DONE]")
        st.stream_body = events
        st.result_queue = [(200, "application/json", _result_body("complete", events.decode()), 0)]
        manifest = _manifest(self.srv.base)
        manifest["task"]["validator"] = {"kind": "json_equal", "expected": {"a": 1}}
        r = pa.run(manifest)
        self.assertFalse(r["answer_quality_pass"])
        self.assertFalse(r["success"])


class TestCLI(Base):
    def test_identity_survives_observer_exit_and_output_is_not_overwritten(self):
        self.srv.state.header_trickle = True
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            output = Path(directory) / "receipt.json"
            manifest.write_text(json.dumps(_manifest(self.srv.base)), encoding="utf-8")
            command = [sys.executable, pa.__file__, "--manifest", str(manifest), "--output", str(output)]
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 4
                while not self.srv.state.requests and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(self.srv.state.requests)
                receipt = json.loads(output.read_text())
                submitted = self.srv.state.requests[0][1]
                self.assertEqual(receipt["request_id"], submitted["request_id"])
                self.assertEqual(receipt["chat_id"], submitted["chat_id"])
                self.assertFalse(receipt["admission_safe"])
                self.assertFalse(receipt["success"])
            finally:
                process.kill()
                process.wait(timeout=5)
            before = output.read_bytes()
            repeated = subprocess.run(command, capture_output=True, timeout=5)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(len(self.srv.state.requests), 1)


class TestTrickle(Base):
    def test_body_trickle_bounded(self):
        st = self.srv.state
        st.stream_trickle = [(0.1, b": alive\n\n")] * 40
        st.result_queue = [(200, "application/json", b'{"state":"unknown","events":""}', 0.0)]
        st.activity_body = b'{"state":"unknown"}'
        t0 = time.monotonic()
        r = self.run_manifest(timeout_s=1, recovery_timeout_s=1)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 3.5)
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertEqual(r["stream_error"], "timeout")
        self.assertFalse(r["success"])

    def test_header_trickle_bounded(self):
        st = self.srv.state
        st.header_trickle = True
        st.stream_body = _sse(_delta("42"), "[DONE]")
        st.result_queue = [(200, "application/json", _result_body("complete", _sse(_delta("42"), "[DONE]").decode()), 0.0)]
        t0 = time.monotonic()
        r = self.run_manifest(timeout_s=1, recovery_timeout_s=1)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 3.5)
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertEqual(r["stream_error"], "timeout")


if __name__ == "__main__":
    unittest.main()
