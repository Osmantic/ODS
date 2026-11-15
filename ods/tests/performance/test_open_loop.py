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
import open_loop  # noqa: E402


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
                    pass

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


def _content(model, text):
    return {"model": model, "choices": [{"index": 0, "delta": {"content": text}}]}


def _finish(model, reason="stop"):
    return {"model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


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


def _load(rate=10.0, lag=1000):
    return {"rate_rps": rate, "max_schedule_lag_ms": lag}


def _ok_handler(s, body):
    _start_chunked(s)
    _chunk(s, _sse(_content("m1", "hello")))
    _chunk(s, _sse(_finish("m1")))
    _chunk(s, _done())
    _end_chunks(s)


class TestLoadConfig(unittest.TestCase):
    def test_valid(self):
        cfg = open_loop.validate_load_config({"rate_rps": 5, "max_schedule_lag_ms": 50})
        self.assertEqual(cfg["rate_rps"], 5.0)
        self.assertEqual(cfg["max_schedule_lag_ms"], 50)

    def test_rejects_out_of_range(self):
        for bad in ({"rate_rps": 0.001, "max_schedule_lag_ms": 50},
                    {"rate_rps": 101, "max_schedule_lag_ms": 50},
                    {"rate_rps": 5, "max_schedule_lag_ms": 0},
                    {"rate_rps": 5, "max_schedule_lag_ms": 1001},
                    {"rate_rps": True, "max_schedule_lag_ms": 50},
                    {"rate_rps": 5}):
            with self.assertRaises(open_loop.LoadConfigError):
                open_loop.validate_load_config(bad)

    def test_rejects_span_over_600s(self):
        m = probe.validate_manifest(_manifest("http://127.0.0.1:1/v1/chat/completions", repetitions=100))
        with self.assertRaises(open_loop.LoadConfigError):
            open_loop.build_arrival_schedule(m, {"rate_rps": 0.01, "max_schedule_lag_ms": 100})


class TestSchedule(unittest.TestCase):
    def test_offsets_monotonic_and_span(self):
        m = probe.validate_manifest(_manifest("http://127.0.0.1:1/v1/chat/completions", repetitions=5))
        sched = open_loop.build_arrival_schedule(m, _load(rate=10.0))
        self.assertEqual(len(sched), 5)
        offsets = [e["scheduled_offset_ms"] for e in sched]
        self.assertEqual(offsets, sorted(offsets))
        self.assertAlmostEqual(offsets[1] - offsets[0], 100.0, places=3)
        self.assertEqual(len({e["request_id"] for e in sched}), 5)


class TestRun(unittest.TestCase):
    def test_all_pass_exit_zero(self):
        with _Server(_ok_handler) as srv, tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out")
            summary = open_loop.run(_manifest(srv.endpoint, repetitions=3), _load(rate=10.0), out)
            self.assertEqual(summary["planned"], 3)
            self.assertEqual(summary["successful_tasks"], 3)
            self.assertEqual(summary["refusals"], 0)
            self.assertAlmostEqual(summary["success_fraction"], 1.0)
            self.assertIsNotNone(summary["goodput_valid_tasks_per_s"])
            self.assertIn("bounded engine/gateway load study", summary["scope"])
            for name in ("manifest.json", "load_config.json", "requests.jsonl", "summary.json"):
                self.assertTrue(os.path.exists(os.path.join(out, name)))

    def test_client_busy_refusal(self):
        def slow(s, body):
            time.sleep(0.3)
            _ok_handler(s, body)
        with _Server(slow) as srv, tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out")
            m = _manifest(srv.endpoint, repetitions=4, concurrency=1)
            summary = open_loop.run(m, _load(rate=100.0, lag=1000), out)
            self.assertEqual(summary["planned"], 4)
            self.assertGreaterEqual(summary["refusals_by_reason"].get("client_busy", 0), 1)
            self.assertEqual(summary["successful_tasks"] + summary["refusals"], 4)
            self.assertEqual(summary["total_records"], 4)

    def test_scheduler_late_refusal(self):
        # Deterministic: patch time.sleep to oversleep past the lag threshold.
        real_sleep = time.sleep
        def oversleep(sec):
            real_sleep(sec + 0.05)
        with _Server(_ok_handler) as srv, tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out")
            m = _manifest(srv.endpoint, repetitions=3, concurrency=2)
            with patch.object(open_loop.time, "sleep", side_effect=oversleep):
                summary = open_loop.run(m, _load(rate=100.0, lag=1), out)
            self.assertGreaterEqual(summary["refusals_by_reason"].get("scheduler_late", 0), 1)
            self.assertEqual(summary["planned"], 3)
            self.assertEqual(summary["total_records"], 3)

    def test_admission_stop_refuses_remaining(self):
        calls = iter([True, False])
        with _Server(_ok_handler) as srv, tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out")
            m = _manifest(srv.endpoint, repetitions=3, concurrency=1)
            summary = open_loop.run(m, _load(rate=100.0), out,
                                    admission_allowed=lambda: next(calls))
            self.assertTrue(summary["admission_stopped"])
            self.assertEqual(summary["planned"], 3)
            self.assertGreaterEqual(summary["refusals_by_reason"].get("admission_stop", 0), 1)
            self.assertEqual(summary["successful_tasks"] + summary["refusals"], 3)
            self.assertEqual(summary["total_records"], 3)

    def test_admission_exception_marks_error_and_persists(self):
        def boom():
            raise RuntimeError("x")
        with _Server(_ok_handler) as srv, tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out")
            m = _manifest(srv.endpoint, repetitions=2, concurrency=1)
            with self.assertRaises(RuntimeError):
                open_loop.run(m, _load(rate=100.0), out, admission_allowed=boom)
            with open(os.path.join(out, "summary.json")) as stream:
                summary = json.load(stream)
            self.assertTrue(summary["admission_error"])
            self.assertGreaterEqual(summary["refusals_by_reason"].get("admission_error", 0), 1)
            self.assertTrue(os.path.exists(os.path.join(out, "summary.json")))
            self.assertEqual(summary["total_records"], 2)

    def test_callback_exception_reraises_after_summary(self):
        # Callback raises on second call; run() must re-raise AFTER persisting
        # summary and marking all remaining arrivals as admission_error.
        calls = {"n": 0}
        def boom():
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("callback boom")
            return True
        with _Server(_ok_handler) as srv, tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out")
            m = _manifest(srv.endpoint, repetitions=3, concurrency=1)
            with self.assertRaises(RuntimeError):
                open_loop.run(m, _load(rate=100.0), out, admission_allowed=boom)
            self.assertTrue(os.path.exists(os.path.join(out, "summary.json")))
            with open(os.path.join(out, "summary.json")) as f:
                summary = json.load(f)
            self.assertTrue(summary["admission_error"])
            self.assertEqual(summary["total_records"], 3)
            with open(os.path.join(out, "requests.jsonl")) as f:
                recs = [json.loads(line) for line in f]
            self.assertEqual(len(recs), 3)
            errs = [r for r in recs if r.get("error") == "admission_error"]
            self.assertGreaterEqual(len(errs), 1)

    def test_refusal_record_shape(self):
        def slow(s, body):
            time.sleep(0.3)
            _ok_handler(s, body)
        with _Server(slow) as srv, tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out")
            m = _manifest(srv.endpoint, repetitions=3, concurrency=1)
            open_loop.run(m, _load(rate=100.0), out)
            with open(os.path.join(out, "requests.jsonl")) as f:
                recs = [json.loads(line) for line in f]
        refusals = [r for r in recs if r.get("error_kind") == "admission"]
        self.assertTrue(refusals)
        for r in refusals:
            self.assertFalse(r["quality_pass"])
            self.assertFalse(r["censored"])
            self.assertFalse(r["transport_complete"])
            self.assertIsNone(r["latency_ms"])
            self.assertIsNone(r["arrival_to_completion_ms"])
            self.assertIn(r["error"], ("client_busy", "scheduler_late", "admission_stop", "admission_error"))
            self.assertEqual(r["task_id"], "t1")
            self.assertIsNotNone(r["decision_offset_ms"])
            if r["error"] in ("admission_stop", "admission_error"):
                self.assertIsNone(r["dispatch_offset_ms"])
                self.assertIsNone(r["schedule_lag_ms"])
            else:
                self.assertIsNone(r["dispatch_offset_ms"])
                self.assertIsNotNone(r["schedule_lag_ms"])

    def test_goodput_uses_elapsed_not_planned(self):
        m = probe.validate_manifest(_manifest("http://127.0.0.1:1/v1/chat/completions", repetitions=4))
        rows = [
            {"task_id": "t1", "quality_pass": True, "error_kind": None,
             "arrival_to_completion_ms": 100, "schedule_lag_ms": 5},
            {"task_id": "t1", "quality_pass": True, "error_kind": None,
             "arrival_to_completion_ms": 200, "schedule_lag_ms": 10},
            {"task_id": "t1", "quality_pass": False, "error_kind": "admission",
             "error": "client_busy", "arrival_to_completion_ms": None, "schedule_lag_ms": 0},
            {"task_id": "t1", "quality_pass": False, "error_kind": "admission",
             "error": "client_busy", "arrival_to_completion_ms": None, "schedule_lag_ms": 0},
        ]
        s = open_loop.summarize(m, _load(), rows, 2.0, 0.3, False, False)
        self.assertEqual(s["planned"], 4)
        self.assertEqual(s["successful_tasks"], 2)
        self.assertAlmostEqual(s["success_fraction"], 0.5)
        self.assertAlmostEqual(s["goodput_valid_tasks_per_s"], 1.0)
        self.assertEqual(s["refusals_by_reason"]["client_busy"], 2)
        self.assertEqual(s["scheduler_lag_max_ms"], 10)
        self.assertIsNone(s["arrival_to_completion_p95_ms"])
        self.assertEqual(s["total_records"], 4)

    def test_scheduler_lag_max_includes_late_refusals(self):
        m = probe.validate_manifest(_manifest("http://127.0.0.1:1/v1/chat/completions", repetitions=2))
        rows = [
            {"task_id": "t1", "quality_pass": True, "error_kind": None,
             "arrival_to_completion_ms": 100, "schedule_lag_ms": 5},
            {"task_id": "t1", "quality_pass": False, "error_kind": "admission",
             "error": "scheduler_late", "arrival_to_completion_ms": None, "schedule_lag_ms": 250},
        ]
        s = open_loop.summarize(m, _load(), rows, 1.0, 0.1, False, False)
        self.assertEqual(s["scheduler_lag_max_ms"], 250)

    def test_elapsed_includes_last_refusal(self):
        # Slow server + client_busy refusals: elapsed must cover the last refusal
        # decision, not just the last completion.
        def slow(s, body):
            time.sleep(0.2)
            _ok_handler(s, body)
        with _Server(slow) as srv, tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out")
            m = _manifest(srv.endpoint, repetitions=4, concurrency=1)
            summary = open_loop.run(m, _load(rate=100.0, lag=1000), out)
            self.assertGreaterEqual(summary["refusals_by_reason"].get("client_busy", 0), 1)
            # Last scheduled arrival is at 30ms; elapsed must be at least that.
            self.assertGreaterEqual(summary["actual_duration_s"], 0.03)

    def test_worker_handoff_is_in_arrival_latency(self):
        real_pool = open_loop.ThreadPoolExecutor

        class DelayedPool(real_pool):
            def submit(self, fn, *args, **kwargs):
                def delayed():
                    time.sleep(.04)
                    return fn(*args, **kwargs)
                return super().submit(delayed)

        with _Server(_ok_handler) as srv, tempfile.TemporaryDirectory() as d:
            with patch.object(open_loop, "ThreadPoolExecutor", DelayedPool):
                open_loop.run(_manifest(srv.endpoint), _load(), os.path.join(d, "out"))
            with open(os.path.join(d, "out", "requests.jsonl")) as stream:
                record = json.loads(stream.readline())
            self.assertGreaterEqual(record["dispatch_offset_ms"], 35)
            self.assertGreaterEqual(record["arrival_to_completion_ms"], 35)
            self.assertLess(record["decision_offset_ms"], record["dispatch_offset_ms"])

    def test_stop_refuses_future_arrivals_without_waiting(self):
        with tempfile.TemporaryDirectory() as d:
            summary = open_loop.run(_manifest("http://127.0.0.1:1/v1/chat/completions", repetitions=2),
                                    _load(rate=.01), os.path.join(d, "out"), admission_allowed=lambda: False)
            self.assertEqual(summary["total_records"], 2)
            self.assertEqual(summary["refusals"], 2)
            self.assertLess(summary["actual_duration_s"], 1)

    def test_invalid_span_creates_no_output(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out")
            m = _manifest("http://127.0.0.1:1/v1/chat/completions", repetitions=100)
            with self.assertRaises(open_loop.LoadConfigError):
                open_loop.run(m, {"rate_rps": 0.01, "max_schedule_lag_ms": 100}, out)
            self.assertFalse(os.path.exists(out))

    def test_existing_outdir_refused(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out")
            os.makedirs(out)
            with self.assertRaises(probe.ManifestError):
                open_loop.run(_manifest("http://127.0.0.1:1/v1/chat/completions"), _load(), out)

    def test_cli_exit_codes(self):
        with _Server(_ok_handler) as srv, tempfile.TemporaryDirectory() as d:
            mp = os.path.join(d, "m.json")
            lp = os.path.join(d, "l.json")
            with open(mp, "w") as f:
                json.dump(_manifest(srv.endpoint), f)
            with open(lp, "w") as f:
                json.dump(_load(rate=10.0), f)
            rc = open_loop.main(["--manifest", mp, "--load-config", lp, "--out", os.path.join(d, "o1")])
            self.assertEqual(rc, 0)
            bad = _manifest(srv.endpoint)
            bad["slo_ms"] = float("nan")
            with open(mp, "w") as f:
                json.dump(bad, f)
            rc = open_loop.main(["--manifest", mp, "--load-config", lp, "--out", os.path.join(d, "o2")])
            self.assertEqual(rc, 2)

    def test_cli_invalid_load_config_exit_2(self):
        with _Server(_ok_handler) as srv, tempfile.TemporaryDirectory() as d:
            mp = os.path.join(d, "m.json")
            lp = os.path.join(d, "l.json")
            with open(mp, "w") as f:
                json.dump(_manifest(srv.endpoint), f)
            with open(lp, "w") as f:
                json.dump({"rate_rps": 0.001, "max_schedule_lag_ms": 50}, f)
            rc = open_loop.main(["--manifest", mp, "--load-config", lp, "--out", os.path.join(d, "o")])
            self.assertEqual(rc, 2)

    def test_no_secret_in_outputs(self):
        selector = "OPEN_LOOP_TEST_AUTH_SELECTOR"
        credential = "fixture-credential-never-publish"
        with _Server(_ok_handler) as srv, tempfile.TemporaryDirectory() as d, \
                patch.dict(os.environ, {selector: credential}):
            out = os.path.join(d, "out")
            m = _manifest(srv.endpoint, api_key_env=selector)
            open_loop.run(m, _load(rate=10.0), out)
            for name in ("manifest.json", "load_config.json", "requests.jsonl", "summary.json"):
                with open(os.path.join(out, name), encoding="utf8") as f:
                    saved = f.read()
                self.assertNotIn(selector, saved)
                self.assertNotIn(credential, saved)


if __name__ == "__main__":
    unittest.main()
