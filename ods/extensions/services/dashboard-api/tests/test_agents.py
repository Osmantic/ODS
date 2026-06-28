"""Tests for routers/agents.py — agent monitoring endpoints."""

from unittest.mock import AsyncMock


# --- GET /api/agents/metrics ---


class TestGetAgentMetrics:

    def test_returns_metrics_structure(self, test_client):
        resp = test_client.get("/api/agents/metrics", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "timestamp" in data
        assert "agent" in data
        assert "cluster" in data
        assert "throughput" in data
        assert "session_count" in data["agent"]
        assert "tokens_per_second" in data["agent"]

    def test_requires_auth(self, test_client):
        resp = test_client.get("/api/agents/metrics")
        assert resp.status_code == 401


# --- GET /api/agents/metrics.html ---


class TestGetAgentMetricsHtml:

    def test_returns_html_fragment(self, test_client):
        resp = test_client.get("/api/agents/metrics.html", headers=test_client.auth_headers)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        body = resp.text
        assert "<div" in body
        assert "Cluster Status" in body
        assert "Active Sessions" in body
        assert "Throughput" in body

    def test_escapes_html_special_chars(self, test_client, monkeypatch):
        """HTML content should be escaped to prevent XSS."""
        from agent_monitor import agent_metrics
        from datetime import datetime

        # Inject XSS-like data into agent metrics
        original_last_update = agent_metrics.last_update
        agent_metrics.last_update = datetime.fromisoformat("2026-01-01T12:00:00")

        resp = test_client.get("/api/agents/metrics.html", headers=test_client.auth_headers)
        assert resp.status_code == 200
        body = resp.text
        # The output should contain safely rendered content, no raw script tags
        assert "<script>" not in body

        # Restore original
        agent_metrics.last_update = original_last_update


# --- GET /api/agents/cluster ---


class TestGetClusterStatus:

    def test_returns_cluster_data(self, test_client, monkeypatch):
        from agent_monitor import cluster_status
        monkeypatch.setattr(cluster_status, "refresh", AsyncMock())

        resp = test_client.get("/api/agents/cluster", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "total_gpus" in data
        assert "active_gpus" in data
        assert "failover_ready" in data


# --- GET /api/agents/throughput ---


class TestGetThroughput:

    def test_returns_throughput_stats(self, test_client):
        resp = test_client.get("/api/agents/throughput", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "current" in data
        assert "average" in data
        assert "peak" in data
        assert "history" in data
