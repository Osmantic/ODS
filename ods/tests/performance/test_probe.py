import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe  # noqa: E402


class _Server:
    def __init__(self, handler_fn):
        outer = self
        self.requests = []
        self.lock = threading.Lock()

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def handle(self):
                try:
                    super().handle()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # Client deliberately closes on DONE, errors and deadlines.

            def log_message(self, *a):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
                with outer.lock:
                    outer.requests.append({"headers": dict(self.headers), "body": body, "path": self.path})
                try:
                    handler_fn(self, body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1/chat/completions"


def _sse(obj):
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")


def _chunk(h, data):
    h.wfile.write(("%x\r\n" % len(data)).encode() + data + b"\r\n")
    h.wfile.flush()


def _end_chunks(h):
    h.wfile.write(b"0\r\n\r\n")
    h.wfile.flush()


def _start_chunked(h):
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.send_header("Transfer-Encoding", "chunked")
    h.end_headers()


def _start_length(h, n):
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.send_header("Content-Length", str(n))
    h.end_headers()


def _content(model, text, finish=None):
    o = {"model": model, "choices": [{"index": 0, "delta": {"content": text}}]}
    if finish:
        o["choices"][0]["finish_reason"] = finish
    return o


def _finish(model, reason="stop"):
    return {"model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


def _usage(pt=5, ct=3):
    return {"usage": {"prompt_tokens": pt, "completion_tokens": ct}}


def _done():
    return b"data: [DONE]\n\n"


def _manifest(endpoint, tasks=None, **kw):
    m = {
        "schema_version": 1,
        "run_label": "t",
        "target": "local",
        "layer": "engine",
        "endpoint": endpoint,
        "model": "m1",
        "provenance": {k: None for k in probe.PROVENANCE_KEYS},
        "cache_condition": "uncontrolled",
        "sampling": {},
        "concurrency": 1,
        "repetitions": 1,
        "timeout_s": 3,
        "slo_ms": 1000,
        "tasks": tasks or [{"id": "t1", "messages": [{"role": "user", "content": "hi"}],
                            "validator": {"kind": "exact", "expected": "hello"}}],
        "schedule_seed": 1,
    }
    m.update(kw)
    return m


def _run(manifest):
    d = tempfile.mkdtemp()
    out = os.path.join(d, "out")
    return probe.run(manifest, out), out


def _rec(manifest):
    m = probe.validate_manifest(manifest)
    return probe.run_request(m, m["tasks"][0], 0, "rid")


class TestProbe(unittest.TestCase):
    def test_auth_reference_and_value_stay_out_of_evidence(self):
        selector = "ODS_PROBE_TEST_AUTH_SELECTOR"
        credential = "fixture-credential-never-publish"
        def h(s, body):
            _start_chunked(s)
            _chunk(s, _sse(_content("m1", "hello")))
            _chunk(s, _sse(_finish("m1")))
            _chunk(s, _done())
            _end_chunks(s)
        with _Server(h) as srv, patch.dict(os.environ, {selector: credential}):
            summary, out = _run(_manifest(srv.endpoint, api_key_env=selector))
            self.assertEqual(summary["quality_pass"], 1)
            self.assertEqual(srv.requests[0]["headers"]["Authorization"], "Bearer " + credential)
        for name in ("manifest.json", "requests.jsonl", "summary.json"):
            with open(os.path.join(out, name), encoding="utf8") as stream:
                saved = stream.read()
            self.assertNotIn(selector, saved)
            self.assertNotIn(credential, saved)

    def test_chunked_valid(self):
        def h(s, body):
            _start_chunked(s)
            _chunk(s, _sse(_content("m1", "hel")))
            _chunk(s, _sse(_content("m1", "lo")))
            _chunk(s, _sse(_finish("m1")))
            _chunk(s, _sse(_usage()))
            _chunk(s, _done())
            _end_chunks(s)
        with _Server(h) as srv:
            r = _rec(_manifest(srv.endpoint))
        self.assertTrue(r["quality_pass"])
        self.assertTrue(r["transport_complete"])
        self.assertEqual(r["usage"]["prompt_tokens"], 5)

    def test_content_length(self):
        def h(s, body):
            payload = _sse(_content("m1", "hello")) + _sse(_finish("m1")) + _sse(_usage()) + _done()
            _start_length(s, len(payload))
            s.wfile.write(payload)
            s.wfile.flush()
        with _Server(h) as srv:
            r = _rec(_manifest(srv.endpoint))
        self.assertTrue(r["quality_pass"])

    def test_missing_usage_null(self):
        def h(s, body):
            _start_chunked(s)
            _chunk(s, _sse(_content("m1", "hello")))
            _chunk(s, _sse(_finish("m1")))
            _chunk(s, _done())
            _end_chunks(s)
        with _Server(h) as srv:
            r = _rec(_manifest(srv.endpoint))
        self.assertTrue(r["quality_pass"])
        self.assertIsNone(r["usage"])

    def test_final_cumulative_usage_wins(self):
        def h(s, body):
            _start_chunked(s)
            _chunk(s, _sse(_usage(5, 0)))
            _chunk(s, _sse(_content("m1", "hello")))
            _chunk(s, _sse(_finish("m1")))
            _chunk(s, _sse(_usage(5, 3)))
            _chunk(s, _done())
            _end_chunks(s)
        with _Server(h) as srv:
            r = _rec(_manifest(srv.endpoint))
        self.assertTrue(r["quality_pass"])
        self.assertEqual(r["usage"]["completion_tokens"], 3)

    def test_role_event_not_first_content(self):
        def h(s, body):
            _start_chunked(s)
            _chunk(s, _sse({"model": "m1", "choices": [{"index": 0, "delta": {"role": "assistant"}}]}))
            _chunk(s, _sse(_content("m1", "hello")))
            _chunk(s, _sse(_finish("m1")))
            _chunk(s, _done())
            _end_chunks(s)
        with _Server(h) as srv:
            r = _rec(_manifest(srv.endpoint))
        self.assertTrue(r["quality_pass"])
        self.assertEqual(r["content_chunk_count"], 1)

    def test_malformed_json(self):
        def h(s, body):
            _start_chunked(s)
            _chunk(s, b"data: {not json}\n\n")
            _end_chunks(s)
        with _Server(h) as srv:
            r = _rec(_manifest(srv.endpoint))
        self.assertEqual(r["error"], "sse_malformed_json")
        self.assertFalse(r["quality_pass"])

    def test_sse_error_event(self):
        def h(s, body):
            _start_chunked(s)
            _chunk(s, _sse({"error": {"message": "boom"}}))
            _end_chunks(s)
        with _Server(h) as srv:
            r = _rec(_manifest(srv.endpoint))
        self.assertEqual(r["error"], "sse_error_event")

    def test_http_500_no_secret(self):
        def h(s, body):
            s.send_response(500)
            s.send_header("Content-Length", "0")
            s.end_headers()
        with _Server(h) as srv:
            r = _rec(_manifest(srv.endpoint))
        self.assertEqual(r["error"], "http_status_500")
        self.assertNotIn("secret", json.dumps(r))

    def test_model_mismatch(self):
        def h(s, body):
            _start_chunked(s)
            _chunk(s, _sse(_content("other", "hello")))
            _chunk(s, _sse(_finish("other")))
            _chunk(s, _done())
            _end_chunks(s)
        with _Server(h) as srv:
            r = _rec(_manifest(srv.endpoint))
        self.assertEqual(r["error"], "response_model_mismatch")
        self.assertEqual(r["error_kind"], "identity")

    def test_finish_length_fail(self):
        def h(s, body):
            _start_chunked(s)
            _chunk(s, _sse(_content("m1", "hello")))
            _chunk(s, _sse(_finish("m1", "length")))
            _chunk(s, _done())
            _end_chunks(s)
        with _Server(h) as srv:
            r = _rec(_manifest(srv.endpoint))
        self.assertEqual(r["error"], "finish_reason_length")
        self.assertEqual(r["error_kind"], "quality")

    def test_wrong_answer(self):
        def h(s, body):
            _start_chunked(s)
            _chunk(s, _sse(_content("m1", "nope")))
            _chunk(s, _sse(_finish("m1")))
            _chunk(s, _done())
            _end_chunks(s)
        with _Server(h) as srv:
            r = _rec(_manifest(srv.endpoint))
        self.assertFalse(r["quality_pass"])
        self.assertEqual(r["error_kind"], "quality")

    def test_missing_done(self):
        def h(s, body):
            _start_chunked(s)
            _chunk(s, _sse(_content("m1", "hello")))
            _chunk(s, _sse(_finish("m1")))
            _end_chunks(s)
        with _Server(h) as srv:
            r = _rec(_manifest(srv.endpoint))
        self.assertEqual(r["error"], "sse_missing_done")

    def test_trickle_timeout_bounded(self):
        def h(s, body):
            _start_chunked(s)
            _chunk(s, b"data: {\"model\":\"m1\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"a\"}}]}\n\n")
            for _ in range(20):
                time.sleep(0.2)
                try:
                    _chunk(s, b": keepalive\n\n")
                except Exception:
                    return
        with _Server(h) as srv:
            m = _manifest(srv.endpoint, timeout_s=1)
            t0 = time.monotonic()
            r = _rec(m)
            elapsed = time.monotonic() - t0
        self.assertEqual(r["error"], "timeout")
        self.assertLess(elapsed, 2.0)

    def test_bool_not_equal_number(self):
        ok, err = probe.validate_answer({"kind": "json_equal", "expected": 1}, "true")
        self.assertFalse(ok)
        ok2, _ = probe.validate_answer({"kind": "json_equal", "expected": True}, "1")
        self.assertFalse(ok2)

    def test_missing_auth_fails_before_mkdir(self):
        d = tempfile.mkdtemp()
        out = os.path.join(d, "out")
        m = _manifest("http://127.0.0.1:1/v1/chat/completions", api_key_env="PROBE_TEST_MISSING_KEY_XYZ")
        os.environ.pop("PROBE_TEST_MISSING_KEY_XYZ", None)
        rc = probe.main(["--manifest", self._write(m, d), "--out", out])
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(out))

    def _write(self, m, d):
        p = os.path.join(d, "m.json")
        with open(p, "w") as f:
            json.dump(m, f)
        return p

    def test_manifest_reserved_and_nan(self):
        base = _manifest("http://127.0.0.1:1/v1/chat/completions")
        bad = dict(base)
        bad["sampling"] = {"model": "x"}
        with self.assertRaises(probe.ManifestError):
            probe.validate_manifest(bad)
        bad2 = dict(base)
        bad2["slo_ms"] = float("nan")
        with self.assertRaises(probe.ManifestError):
            probe.validate_manifest(bad2)

    def test_existing_outdir_refused(self):
        d = tempfile.mkdtemp()
        out = os.path.join(d, "out")
        os.makedirs(out)
        m = _manifest("http://127.0.0.1:1/v1/chat/completions")
        with self.assertRaises(probe.ManifestError):
            probe.run(m, out)

    def test_summary_mixed(self):
        def h(s, body):
            _start_chunked(s)
            _chunk(s, _sse(_content("m1", "hello")))
            _chunk(s, _sse(_finish("m1")))
            _chunk(s, _done())
            _end_chunks(s)
        with _Server(h) as srv:
            tasks = [
                {"id": "a", "messages": [{"role": "user", "content": "x"}],
                 "validator": {"kind": "exact", "expected": "hello"}},
                {"id": "b", "messages": [{"role": "user", "content": "x"}],
                 "validator": {"kind": "exact", "expected": "wrong"}},
            ]
            m = _manifest(srv.endpoint, tasks=tasks, repetitions=2)
            summary, out = _run(m)
        self.assertEqual(summary["scheduled"], 4)
        self.assertEqual(summary["quality_pass"], 2)
        self.assertEqual(summary["quality_failures"], 2)
        self.assertAlmostEqual(summary["quality_pass_fraction"], 0.5)
        self.assertIsNotNone(summary["successful_task_latency_p50_ms"])

    def test_output_jsonl_no_secrets(self):
        def h(s, body):
            _start_chunked(s)
            _chunk(s, _sse(_content("m1", "hello")))
            _chunk(s, _sse(_finish("m1")))
            _chunk(s, _done())
            _end_chunks(s)
        with _Server(h) as srv:
            m = _manifest(srv.endpoint)
            summary, out = _run(m)
        with open(os.path.join(out, "requests.jsonl")) as f:
            line = f.readline()
        rec = json.loads(line)
        self.assertNotIn("hello", line)
        self.assertNotIn("Authorization", line)
        self.assertIsNotNone(rec["response_sha256"])

    def test_worker_exception_keeps_identity(self):
        m = probe.validate_manifest(_manifest("http://127.0.0.1:1/v1/chat/completions"))
        orig = probe.run_request
        def boom(*a, **k):
            raise RuntimeError("x")
        probe.run_request = boom
        try:
            d = tempfile.mkdtemp()
            out = os.path.join(d, "out")
            probe.run(m, out)
        finally:
            probe.run_request = orig
        with open(os.path.join(out, "requests.jsonl")) as f:
            rec = json.loads(f.readline())
        self.assertEqual(rec["task_id"], "t1")
        self.assertIn("worker_exception", rec["error"])

    def test_utf8_split_chunk(self):
        def h(s, body):
            _start_chunked(s)
            payload = _sse(_content("m1", "hÃ©llo"))
            mid = payload.index(b"\xc3") + 1
            _chunk(s, payload[:mid])
            _chunk(s, payload[mid:])
            _chunk(s, _sse(_finish("m1")))
            _chunk(s, _done())
            _end_chunks(s)
        with _Server(h) as srv:
            m = _manifest(srv.endpoint, tasks=[{"id": "t1", "messages": [{"role": "user", "content": "x"}],
                                                "validator": {"kind": "exact", "expected": "hÃ©llo"}}])
            r = _rec(m)
        self.assertTrue(r["quality_pass"])

    def test_json_duplicate_keys_rejected(self):
        passed, _ = probe.validate_answer({"kind": "json_equal", "expected": {"x": 2}}, '{"x": 1, "x": 2}')
        self.assertFalse(passed)

    def test_summary_excludes_fast_errors_and_censored_times(self):
        m = probe.validate_manifest(_manifest("http://127.0.0.1:9/v1/chat/completions", repetitions=4))
        rows = [
            dict(task_id="t1", latency_ms=100, transport_complete=True, quality_pass=True, error_kind=None),
            dict(task_id="t1", latency_ms=1, transport_complete=False, quality_pass=False, error_kind="transport"),
            dict(task_id="t1", latency_ms=200, transport_complete=True, quality_pass=False, error_kind="quality"),
            dict(task_id="t1", latency_ms=1000, transport_complete=False, quality_pass=False, error_kind="transport", censored=True),
        ]
        result = probe.summarize(m, rows, 2, False, 4)
        self.assertEqual(result["quality_pass_fraction"], 0.25)
        self.assertEqual(result["transport_failures"], 2)
        self.assertEqual(result["censored"], 1)
        self.assertEqual(result["latency_p50_ms"], 150)
        self.assertEqual(result["successful_task_latency_p50_ms"], 100)
        self.assertEqual(result["slo_goodput_tasks_per_s"], 0.5)

    def test_delay_first_chunk_under_timeout(self):
        def h(s, body):
            _start_chunked(s)
            time.sleep(1.2)
            _chunk(s, _sse(_content("m1", "hello")))
            _chunk(s, _sse(_finish("m1")))
            _chunk(s, _done())
            _end_chunks(s)
        with _Server(h) as srv:
            r = _rec(_manifest(srv.endpoint, timeout_s=3))
        self.assertTrue(r["quality_pass"])
        self.assertGreater(r["first_content_ms"], 1000)


if __name__ == "__main__":
    unittest.main()
