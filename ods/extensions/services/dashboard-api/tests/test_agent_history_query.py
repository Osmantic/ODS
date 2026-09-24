"""Operators can request retained history without changing summary semantics."""

import pytest

import agent_monitor
from routers import agents


@pytest.fixture()
def populated_history(monkeypatch):
    history = agent_monitor.ThroughputMetrics()
    for value in range(100):
        history.add_sample(float(value))
    monkeypatch.setattr(agents, "throughput", history)
    return history


@pytest.mark.parametrize("limit", [1, 60, 180])
def test_throughput_returns_requested_history_and_full_window_summary(
    test_client, populated_history, limit
):
    response = test_client.get(
        f"/api/agents/throughput?limit={limit}", headers=test_client.auth_headers
    )
    assert response.status_code == 200
    result = response.json()
    assert result["history"] == populated_history.data_points[-limit:]
    assert result["current"] == 99
    assert result["average"] == 49.5
    assert result["peak"] == 99


def test_default_history_contract_remains_thirty_samples(test_client, populated_history):
    response = test_client.get("/api/agents/throughput", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json()["history"] == populated_history.data_points[-30:]


@pytest.mark.parametrize("limit", ["0", "-1", "181", "1.5", "all"])
def test_history_query_rejects_invalid_limits(test_client, limit):
    response = test_client.get(
        f"/api/agents/throughput?limit={limit}", headers=test_client.auth_headers
    )
    assert response.status_code == 422


def test_extended_history_still_requires_authentication(test_client):
    assert test_client.get("/api/agents/throughput?limit=180").status_code == 401
