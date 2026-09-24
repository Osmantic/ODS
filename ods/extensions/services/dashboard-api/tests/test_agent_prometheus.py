"""Scrapes use the same authenticated agent snapshot as the JSON API."""

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import agent_monitor
from routers import agents
from security import DASHBOARD_API_KEY


@pytest.fixture()
def client(monkeypatch):
    monitor = agent_monitor.ThroughputMetrics()
    monkeypatch.setattr(agent_monitor, "throughput", monitor)
    monkeypatch.setattr(agent_monitor, "agent_metrics", agent_monitor.AgentMetrics())
    monkeypatch.setattr(agent_monitor, "cluster_status", agent_monitor.ClusterStatus())
    app = FastAPI()
    app.include_router(agents.router)
    with TestClient(app) as client:
        yield client


def test_scrape_requires_bearer_credentials(client):
    assert client.get("/api/agents/metrics.prom").status_code == 401
    assert client.get("/api/agents/metrics.prom", headers={
        "Authorization": "Bearer incorrect"
    }).status_code == 403


def test_scrape_exports_snapshot_without_placeholder_metrics(client):
    agent_monitor.agent_metrics.session_count = 4
    cluster = agent_monitor.cluster_status
    cluster.total_gpus, cluster.active_gpus, cluster.failover_ready = 3, 2, True
    agent_monitor.throughput.add_sample(1.25)
    sampled_at = datetime.fromisoformat(
        agent_monitor.throughput.data_points[-1]["timestamp"]
    ).timestamp()
    response = client.get("/api/agents/metrics.prom", headers={
        "Authorization": f"Bearer {DASHBOARD_API_KEY}"
    })
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert response.text.endswith("\n")
    lines = response.text.splitlines()
    samples = dict(line.split() for line in lines if not line.startswith("#"))
    assert samples == {
        "ods_agent_summary_entries": "4",
        "ods_cluster_gpus": "3",
        "ods_cluster_healthy_gpus": "2",
        "ods_cluster_failover_ready": "1",
        "ods_agent_output_tokens_per_second_24h": "1.25",
        "ods_agent_throughput_sample_timestamp_seconds": str(sampled_at),
    }
    for name in samples:
        assert f"# TYPE {name} gauge" in lines
        assert sum(line.startswith(f"# HELP {name} ") for line in lines) == 1
    assert "queue_depth" not in response.text
    assert "error_rate" not in response.text


def test_scrape_distinguishes_no_observation_from_observed_zero(client):
    headers = {"Authorization": f"Bearer {DASHBOARD_API_KEY}"}
    empty = client.get("/api/agents/metrics.prom", headers=headers)
    assert empty.status_code == 200
    assert "ods_agent_output_tokens_per_second_24h" not in empty.text
    assert "ods_agent_throughput_sample_timestamp_seconds" not in empty.text
    agent_monitor.throughput.add_sample(0.0)
    observed = client.get("/api/agents/metrics.prom", headers=headers)
    assert "ods_agent_output_tokens_per_second_24h 0.0\n" in observed.text
    assert "ods_agent_throughput_sample_timestamp_seconds " in observed.text
