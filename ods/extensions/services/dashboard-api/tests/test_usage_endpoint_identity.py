"""HTTP regression for independent local runtime observation baselines."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import json

import pytest


@contextmanager
def metrics_server(initial_tokens):
    counters = {"tokens": initial_tokens, "status": 200, "additional": {}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            report = self.path.startswith("/api/report?")
            sample = counters["additional"].get(self.path, counters)
            body = json.dumps(counters["report"]).encode() if report else (
                f"llamacpp:prompt_tokens_total {sample['tokens']}\n"
                f"llamacpp:tokens_predicted_total {sample['tokens']}\n"
            ).encode()
            self.send_response(200 if report else sample["status"])
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            # In-process test server; pytest assertions are the output.
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/metrics", counters
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("same_port", [False, True], ids=["different-ports", "different-paths"])
def test_report_keeps_same_host_runtime_baselines_independent(test_client, monkeypatch, same_port):
    from routers import usage

    monkeypatch.setattr(usage, "_LOCAL_RUNTIME_REQUEST_STATE", {})
    monkeypatch.setattr(usage, "_runtime_model_name", lambda: "shared-model.gguf")
    original_report = usage._empty_report("2026-05-01", "2026-05-02", status="ok")
    with metrics_server(10) as (first_url, first), metrics_server(100) as (second_url, second):
        first["report"] = original_report
        monkeypatch.setattr(usage, "TOKEN_SPY_URL", first_url.removesuffix("/metrics"))
        if same_port:
            first["additional"]["/alternate-metrics"] = second
            second_url = first_url.removesuffix("/metrics") + "/alternate-metrics"
        monkeypatch.setenv("LOCAL_USAGE_METRICS_URLS", f"{first_url},{second_url}")

        def report():
            response = test_client.get(
                "/api/usage/report?start=2026-05-01&end=2026-05-02",
                headers=test_client.auth_headers,
            )
            assert response.status_code == 200
            data = response.json()
            # Cumulative observations must remain outside date-bounded totals.
            assert data["summary"] == original_report["summary"]
            local = data["source"]["local_runtime"]
            assert local["included_in_totals"] is False
            return [counter["requests"] for counter in local["counters"]]

        assert report() == [0, 0]
        first["tokens"] = 20
        second["tokens"] = 110
        assert report() == [1, 1]
        # Restarting one runtime must not reset the other runtime's history.
        first["tokens"] = 1
        second["tokens"] = 120
        assert report() == [0, 2]
        # A failed sample neither advances nor erases either runtime's baseline.
        second["status"] = 503
        first["tokens"] = 2
        second["tokens"] = 130
        assert report() == [1]
        second["status"] = 200
        assert report() == [1, 3]
