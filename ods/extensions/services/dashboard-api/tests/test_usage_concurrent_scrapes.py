"""Report-boundary proof of bounded, concurrent HTTP runtime scrapes."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Barrier, BrokenBarrierError, Lock, Thread
from unittest.mock import AsyncMock


def test_report_scrapes_in_bounded_waves_and_preserves_order(test_client, monkeypatch):
    from routers import usage

    barrier = Barrier(4)
    lock = Lock()
    state = {"active": 0, "peak": 0}
    failures = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/failed":
                self.send_error(503)
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
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = usage._empty_report("2026-05-01", "2026-05-02", status="ok")
    monkeypatch.setattr(usage, "_fetch_token_spy_report", AsyncMock(return_value=original))
    urls = [f"http://127.0.0.1:{server.server_port}/{i}" for i in range(1, 9)]
    urls.append(f"http://127.0.0.1:{server.server_port}/failed")
    monkeypatch.setenv("LOCAL_USAGE_METRICS_URLS", ",".join(urls))
    try:
        response = test_client.get(
            "/api/usage/report?start=2026-05-01&end=2026-05-02",
            headers=test_client.auth_headers,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert response.status_code == 200
    assert failures == [], "Independent runtime requests never overlapped"
    assert state["peak"] == 4
    report = response.json()
    assert report["summary"] == original["summary"]
    local = report["source"]["local_runtime"]
    assert local["included_in_totals"] is False
    assert [item["input_tokens"] for item in local["counters"]] == list(range(1, 9))
