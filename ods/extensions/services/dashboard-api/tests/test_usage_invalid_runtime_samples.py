"""A bad optional metrics exporter must not erase the authenticated Usage report."""

import copy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from routers import usage


@pytest.fixture
def telemetry(monkeypatch):
    original = usage._empty_report("2026-09-01", "2026-09-02", status="ok")
    original["summary"].update(requests=7, input_tokens=321, spend_usd=1.25)
    state = {"sample": {}, "paths": [], "report": original}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["paths"].append(self.path)
            if self.path.startswith("/api/report?"):
                body = json.dumps(state["report"]).encode()
            else:
                sample = state.get("additional", {}).get(self.path, state["sample"])
                body = "\n".join(f"llamacpp:{name} {value}" for name, value in sample.items()).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    state["url"] = url
    monkeypatch.setattr(usage, "TOKEN_SPY_URL", url)
    monkeypatch.setattr(usage, "TOKEN_SPY_API_KEY", "fixture-token-key")
    monkeypatch.setenv("LOCAL_USAGE_METRICS_URLS", url + "/metrics")
    monkeypatch.setattr(usage, "_LOCAL_RUNTIME_REQUEST_STATE", {})
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def report(client):
    response = client.get("/api/usage/report?start=2026-09-01&end=2026-09-02", headers=client.auth_headers)
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize("metric", ["prompt_tokens_total", "tokens_predicted_total", "requests_total"])
@pytest.mark.parametrize("invalid", ["1e999", "-1e999", "NaN", "+Inf", "-1", "broken"])
def test_invalid_exporter_sample_preserves_report_and_observation_baseline(test_client, telemetry, metric, invalid):
    # Initialize a real missing-request-counter baseline before the bad scrape.
    telemetry["sample"] = {"prompt_tokens_total": "100", "tokens_predicted_total": "50"}
    first = report(test_client)
    assert first["source"]["local_runtime"]["counters"][0]["requests"] == 0
    baseline = copy.deepcopy(usage._LOCAL_RUNTIME_REQUEST_STATE)

    telemetry["sample"] = {"prompt_tokens_total": "120", "tokens_predicted_total": "70", metric: invalid}
    failed_sample = report(test_client)
    assert failed_sample["source"]["status"] == "ok"
    assert failed_sample["summary"] == telemetry["report"]["summary"]
    assert "local_runtime" not in failed_sample["source"]
    assert usage._LOCAL_RUNTIME_REQUEST_STATE == baseline

    telemetry["sample"] = {"prompt_tokens_total": "140", "tokens_predicted_total": "90"}
    recovered = report(test_client)
    assert recovered["summary"] == first["summary"]
    counter = recovered["source"]["local_runtime"]["counters"][0]
    assert (counter["input_tokens"], counter["output_tokens"], counter["requests"]) == (140, 90, 1)
    assert recovered["source"]["local_runtime"]["included_in_totals"] is False
    assert telemetry["paths"].count("/metrics") == 3


def test_valid_zero_and_scientific_notation_remain_observable(test_client, telemetry):
    telemetry["sample"] = {"prompt_tokens_total": "0", "tokens_predicted_total": "1.5e3", "requests_total": "0"}
    result = report(test_client)
    counter = result["source"]["local_runtime"]["counters"][0]
    assert (counter["input_tokens"], counter["output_tokens"], counter["requests"]) == (0, 1500, 0)
    assert counter["request_count_source"] == "prometheus_counter"
    assert result["summary"] == telemetry["report"]["summary"]


def test_a_bad_exporter_does_not_hide_a_healthy_exporter(test_client, telemetry, monkeypatch):
    telemetry["sample"] = {"prompt_tokens_total": "1e999", "tokens_predicted_total": "50"}
    telemetry["additional"] = {"/valid": {"prompt_tokens_total": "75", "tokens_predicted_total": "25", "requests_total": "3"}}
    monkeypatch.setenv("LOCAL_USAGE_METRICS_URLS", telemetry["url"] + "/metrics," + telemetry["url"] + "/valid")
    result = report(test_client)
    counters = result["source"]["local_runtime"]["counters"]
    assert len(counters) == 1
    assert (counters[0]["input_tokens"], counters[0]["output_tokens"], counters[0]["requests"]) == (75, 25, 3)
    assert result["summary"] == telemetry["report"]["summary"]
