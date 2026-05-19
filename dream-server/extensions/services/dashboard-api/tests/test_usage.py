"""Usage/cost report proxy tests."""

from __future__ import annotations

from unittest.mock import AsyncMock


def _usage_payload():
    return {
        "period": {"start": "2026-05-01", "end": "2026-05-31"},
        "source": {"name": "token-spy", "status": "ok", "detail": None},
        "summary": {
            "spend_usd": 1.25,
            "requests": 2,
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 25,
            "cache_write_tokens": 0,
            "total_tokens": 175,
            "tracked_providers": 1,
            "billing_providers": 1,
            "local_providers": 0,
            "untracked_providers": 0,
            "paid_cost_usd": 1.25,
            "local_cost_usd": 0,
        },
        "daily": [],
        "models": [],
        "services": [],
        "sources": [],
    }


def test_usage_report_requires_auth(test_client):
    resp = test_client.get("/api/usage/report?start=2026-05-01&end=2026-05-31")
    assert resp.status_code == 401


def test_usage_report_returns_token_spy_payload(test_client, monkeypatch):
    import routers.usage as usage_router

    fetch = AsyncMock(return_value=_usage_payload())
    monkeypatch.setattr(usage_router, "_fetch_token_spy_report", fetch)
    monkeypatch.setattr(usage_router, "_fetch_local_runtime_counters", AsyncMock(return_value=[]))

    resp = test_client.get(
        "/api/usage/report?start=2026-05-01&end=2026-05-31",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["spend_usd"] == 1.25
    assert data["source"]["status"] == "ok"
    fetch.assert_awaited_once_with("2026-05-01", "2026-05-31")


def test_usage_report_returns_honest_empty_payload_when_token_spy_disabled(
    test_client,
    monkeypatch,
):
    import routers.usage as usage_router

    monkeypatch.setattr(usage_router, "TOKEN_SPY_URL", "")
    monkeypatch.setattr(usage_router, "_fetch_local_runtime_counters", AsyncMock(return_value=[]))

    resp = test_client.get(
        "/api/usage/report?start=2026-05-01&end=2026-05-03",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"]["status"] == "unavailable"
    assert data["summary"]["spend_usd"] == 0
    assert data["summary"]["requests"] == 0
    assert [day["date"] for day in data["daily"]] == [
        "2026-05-01",
        "2026-05-02",
        "2026-05-03",
    ]


def test_usage_report_rejects_reversed_date_range(test_client):
    resp = test_client.get(
        "/api/usage/report?start=2026-05-31&end=2026-05-01",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"]["status"] == "invalid_range"
    assert data["source"]["detail"] == "end must be on or after start"


def test_usage_report_surfaces_local_runtime_counters_without_date_bucketing(test_client, monkeypatch):
    import routers.usage as usage_router

    fetch = AsyncMock(return_value=usage_router._empty_report("2026-05-01", "2026-05-31", status="ok"))
    counters = [
        {
            "runtime": "llama.cpp",
            "adapter": "prometheus",
            "service": "llama-server",
            "model": "Qwen3.5-9B-Q4_K_M.gguf",
            "input_tokens": 178,
            "output_tokens": 62,
            "requests": 0,
            "request_count_available": False,
            "request_count_source": "unavailable",
        }
    ]
    monkeypatch.setattr(usage_router, "_fetch_token_spy_report", fetch)
    monkeypatch.setattr(usage_router, "_fetch_local_runtime_counters", AsyncMock(return_value=counters))
    resp = test_client.get(
        "/api/usage/report?start=2026-05-01&end=2026-05-31",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["input_tokens"] == 0
    assert data["summary"]["output_tokens"] == 0
    assert data["summary"]["total_tokens"] == 0
    assert data["summary"]["local_providers"] == 0
    assert data["summary"]["spend_usd"] == 0
    assert data["models"] == []
    assert data["daily"][15]["input_tokens"] == 0
    assert data["source"]["local_runtime"] == {
        "status": "observed",
        "detail": "Cumulative llama.cpp counters observed but not merged into date-bounded totals because Prometheus counters do not include event timestamps",
        "included_in_totals": False,
        "adapters": ["llama.cpp"],
        "counters": [
            {
                "runtime": "llama.cpp",
                "service": "llama-server",
                "model": "Qwen3.5-9B-Q4_K_M.gguf",
                "input_tokens": 178,
                "output_tokens": 62,
                "requests": 0,
                "request_count_source": "unavailable",
            }
        ],
        "request_count_available": False,
        "request_count_sources": ["unavailable"],
        "request_count_note": None,
    }


def test_usage_report_keeps_token_spy_local_rows_in_date_range(test_client, monkeypatch):
    import routers.usage as usage_router

    payload = usage_router._empty_report("2026-05-01", "2026-05-31", status="ok")
    payload["models"].append(
        {
            "model": "Qwen3.5-9B-Q4_K_M.gguf",
            "provider": "local",
            "service": "llama-server",
            "input_tokens": 178,
            "output_tokens": 62,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "requests": 0,
            "cost_usd": 0,
            "cost_source": "local_zero_cost",
        }
    )
    payload["summary"]["input_tokens"] = 178
    payload["summary"]["output_tokens"] = 62
    payload["summary"]["total_tokens"] = 240
    payload["summary"]["local_providers"] = 1
    payload["daily"][15]["input_tokens"] = 178
    monkeypatch.setattr(usage_router, "_fetch_token_spy_report", AsyncMock(return_value=payload))
    monkeypatch.setattr(usage_router, "_fetch_local_runtime_counters", AsyncMock(return_value=[]))

    resp = test_client.get(
        "/api/usage/report?start=2026-05-01&end=2026-05-31",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["input_tokens"] == 178
    assert data["summary"]["local_providers"] == 1
    assert data["models"][0]["cost_source"] == "local_zero_cost"


def test_llama_cpp_prometheus_metrics_are_detected_as_local_runtime(monkeypatch):
    import routers.usage as usage_router

    monkeypatch.setenv("GGUF_FILE", "Qwen3.5-9B-Q4_K_M.gguf")
    usage_router._LOCAL_RUNTIME_REQUEST_STATE.clear()
    metrics = "\n".join(
        [
            "llamacpp:prompt_tokens_total 178",
            "llamacpp:tokens_predicted_total 62",
        ]
    )

    counters = usage_router._extract_llama_cpp_prometheus_counters(
        metrics,
        "http://llama-server:8080/metrics",
    )

    assert counters == {
        "runtime": "llama.cpp",
        "adapter": "prometheus",
        "service": "llama-server",
        "model": "Qwen3.5-9B-Q4_K_M.gguf",
        "input_tokens": 178,
        "output_tokens": 62,
        "requests": 0,
        "request_count_available": False,
        "request_count_source": "unavailable",
        "request_count_note": "llama.cpp did not expose a request counter; baseline initialized from current token counters",
    }


def test_llama_cpp_prometheus_request_count_uses_observed_delta_when_counter_is_missing(monkeypatch):
    import routers.usage as usage_router

    monkeypatch.setenv("GGUF_FILE", "Qwen3.5-9B-Q4_K_M.gguf")
    usage_router._LOCAL_RUNTIME_REQUEST_STATE.clear()

    first = usage_router._extract_llama_cpp_prometheus_counters(
        "\n".join(
            [
                "llamacpp:prompt_tokens_total 100",
                "llamacpp:tokens_predicted_total 50",
            ]
        ),
        "http://llama-server:8080/metrics",
    )
    second = usage_router._extract_llama_cpp_prometheus_counters(
        "\n".join(
            [
                "llamacpp:prompt_tokens_total 125",
                "llamacpp:tokens_predicted_total 75",
            ]
        ),
        "http://llama-server:8080/metrics",
    )

    assert first["requests"] == 0
    assert first["request_count_available"] is False
    assert second["requests"] == 1
    assert second["request_count_available"] is True
    assert second["request_count_source"] == "observed_counter_delta"


def test_usage_token_spy_key_falls_back_to_shared_data_file(tmp_path, monkeypatch):
    import routers.usage as usage_router

    key_file = tmp_path / "token-spy-api-key.txt"
    key_file.write_text("file-key", encoding="utf-8")
    monkeypatch.setattr(usage_router, "TOKEN_SPY_API_KEY", "")
    monkeypatch.setattr(usage_router, "TOKEN_SPY_KEY_FILE", key_file)

    assert usage_router._token_spy_api_key() == "file-key"
