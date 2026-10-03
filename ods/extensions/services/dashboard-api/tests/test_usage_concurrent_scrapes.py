"""Report-boundary proof of bounded, concurrent HTTP runtime scrapes."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Barrier, BrokenBarrierError, Lock, Thread
import asyncio
import json
import pytest


def test_report_scrapes_in_bounded_waves_and_preserves_order(test_client, monkeypatch):
    from routers import usage

    barrier = Barrier(4)
    lock = Lock()
    state = {"active": 0, "peak": 0, "failed": True}
    failures = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api/report?"):
                body = json.dumps(original).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/failed":
                if state["failed"]:
                    self.send_error(503)
                else:
                    body = b"llamacpp:prompt_tokens_total 99\nllamacpp:tokens_predicted_total 198\nllamacpp:requests_total 3\n"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                return
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            try:
                barrier.wait(timeout=2)
            except BrokenBarrierError:
                failures.append(self.path)
            tokens = int(self.path[1:])
            body = (
                f"llamacpp:prompt_tokens_total {tokens}\n"
                f"llamacpp:tokens_predicted_total {tokens * 2}\n"
                "llamacpp:requests_total 3\n"
            ).encode()
            # This gauges concurrent upstream work, excluding response delivery.
            with lock:
                state["active"] -= 1
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    original = usage._empty_report("2026-05-01", "2026-05-02", status="ok")
    monkeypatch.setattr(usage, "TOKEN_SPY_URL", f"http://127.0.0.1:{server.server_port}")
    urls = [f"http://127.0.0.1:{server.server_port}/{i}" for i in range(1, 9)]
    urls.append(f"http://127.0.0.1:{server.server_port}/failed")
    monkeypatch.setenv("LOCAL_USAGE_METRICS_URLS", ",".join(urls))
    try:
        response = test_client.get(
            "/api/usage/report?start=2026-05-01&end=2026-05-02",
            headers=test_client.auth_headers,
        )
        state["failed"] = False
        recovery = test_client.get(
            "/api/usage/report?start=2026-05-01&end=2026-05-02", headers=test_client.auth_headers,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert response.status_code == 200
    assert failures == [], "Independent runtime requests never overlapped"
    assert state["peak"] == 4
    report = response.json()
    assert report["summary"] == original["summary"]
    local = report["source"]["local_runtime"]
    assert local["included_in_totals"] is False
    assert [item["input_tokens"] for item in local["counters"]] == list(range(1, 9))
    assert recovery.status_code == 200
    assert [item["input_tokens"] for item in recovery.json()["source"]["local_runtime"]["counters"]] == [*range(1, 9), 99]


@pytest.mark.asyncio
async def test_cancellation_does_not_parse_partial_scrapes_or_start_queued_requests(monkeypatch):
    from threading import Event
    from routers import usage

    entered, release = Event(), Event()
    paths = []
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            with lock:
                paths.append(self.path)
                if len(paths) == 4:
                    entered.set()
            release.wait(timeout=5)
            body = b"llamacpp:prompt_tokens_total 10\nllamacpp:tokens_predicted_total 20\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    monkeypatch.setenv("LOCAL_USAGE_METRICS_URLS", ",".join(
        f"http://127.0.0.1:{server.server_port}/{index}" for index in range(8)))
    monkeypatch.setattr(usage, "_LOCAL_RUNTIME_REQUEST_STATE", {})
    task = asyncio.create_task(usage._fetch_local_runtime_counters())
    try:
        assert await asyncio.to_thread(entered.wait, 2), "Four independent reads must start"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert usage._LOCAL_RUNTIME_REQUEST_STATE == {}
        release.set()
        # A new request can recover; the abandoned request cannot publish state.
        results = await usage._fetch_local_runtime_counters()
        assert len(results) == 8
        assert len(paths) == 12, "The four cancelled queued reads must never start"
    finally:
        release.set()
        task.cancel()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.asyncio
async def test_real_upstream_body_timeout_does_not_hide_healthy_runtime(monkeypatch):
    from threading import Event
    from routers import usage

    release = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            if self.path == "/stall":
                self.send_header("Content-Length", "1")
                self.end_headers()
                self.wfile.flush()
                release.wait(timeout=10)
                return
            body = b"llamacpp:prompt_tokens_total 10\nllamacpp:tokens_predicted_total 20\nllamacpp:requests_total 3\n"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("LOCAL_USAGE_METRICS_URLS", f"{base}/stall,{base}/metrics")
    try:
        result = await asyncio.wait_for(usage._fetch_local_runtime_counters(), timeout=8)
        assert len(result) == 1
        assert result[0]["input_tokens"] == 10
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
