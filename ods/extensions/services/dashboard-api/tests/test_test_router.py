"""Tests for routers/test.py — ODS capability test endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Auth Enforcement
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/test/llm", "GET"),
        ("/api/test/voice", "GET"),
        ("/api/test/rag", "GET"),
        ("/api/test/workflows", "GET"),
        ("/api/test/search", "GET"),
    ],
)
def test_endpoints_require_auth(test_client, path, method):
    """All capability test endpoints must reject requests without an API key."""
    resp = test_client.request(method, path)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/test/llm
# ---------------------------------------------------------------------------

def test_llm_test_endpoint_healthy(test_client, monkeypatch):
    """Should return success=True when the llama-server service is healthy."""
    from config import SERVICES
    monkeypatch.setitem(
        SERVICES,
        "llama-server",
        {"name": "Llama Server", "port": 8080, "health": "/health", "type": "docker"},
    )
    monkeypatch.setattr(
        "helpers.check_service_health",
        AsyncMock(return_value=MagicMock(status="healthy")),
    )

    resp = test_client.get("/api/test/llm", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"success": True}


def test_llm_test_endpoint_unhealthy(test_client, monkeypatch):
    """Should return success=False when the llama-server service is down."""
    from config import SERVICES
    monkeypatch.setitem(
        SERVICES,
        "llama-server",
        {"name": "Llama Server", "port": 8080, "health": "/health", "type": "docker"},
    )
    monkeypatch.setattr(
        "helpers.check_service_health",
        AsyncMock(return_value=MagicMock(status="down")),
    )

    resp = test_client.get("/api/test/llm", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"success": False}


# ---------------------------------------------------------------------------
# GET /api/test/voice
# ---------------------------------------------------------------------------

def test_voice_test_endpoint(test_client, monkeypatch):
    """Should return success based on voice status availability."""
    monkeypatch.setattr(
        "routers.voice.voice_status",
        AsyncMock(return_value={"available": True}),
    )

    resp = test_client.get("/api/test/voice", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"success": True}


# ---------------------------------------------------------------------------
# GET /api/test/rag
# ---------------------------------------------------------------------------

def test_rag_test_endpoint_healthy(test_client, monkeypatch):
    """Should return success=True when Qdrant is healthy."""
    from config import SERVICES
    monkeypatch.setitem(
        SERVICES,
        "qdrant",
        {"name": "Qdrant", "port": 6333, "health": "/healthz", "type": "docker"},
    )
    monkeypatch.setattr(
        "helpers.check_service_health",
        AsyncMock(return_value=MagicMock(status="healthy")),
    )

    resp = test_client.get("/api/test/rag", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"success": True}


# ---------------------------------------------------------------------------
# GET /api/test/workflows
# ---------------------------------------------------------------------------

def test_workflows_test_endpoint(test_client, monkeypatch):
    """Should return success based on n8n availability."""
    monkeypatch.setattr(
        "routers.workflows.check_n8n_available",
        AsyncMock(return_value=True),
    )

    resp = test_client.get("/api/test/workflows", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"success": True}


# ---------------------------------------------------------------------------
# GET /api/test/search (Regression & Capability Matrix Checks)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "hits,tool_called,agent_reply,expected_verdict,expected_reason,expected_success",
    [
        # Regression 1: SearXNG has zero hits, tool called, claims empty.
        (0, True, "All searches returned empty results.", "skip", "search_engines_throttled", False),
        # Regression 2: SearXNG has hits, tool called, agent omits URLs.
        (5, True, "I searched but found nothing.", "fail", "agent_did_not_surface_results", False),
        # Regression 3: Happy path. Agent returns a URL.
        (5, True, "Here's what I found: http://example.com", "ok", None, True),
        # Regression 4: Agent didn't invoke the web_search tool.
        (5, False, "No search.", "fail", "agent_did_not_invoke_web_search", False),
        # Regression 5: SearXNG is unreachable.
        (-1, True, "Error.", "skip", "searxng_unreachable", False),
    ],
    ids=[
        "engines_throttled",
        "agent_ignored_results",
        "happy_path_ok",
        "agent_skipped_tool",
        "searxng_down",
    ],
)
def test_search_probe_regression_matrix(
    test_client,
    monkeypatch,
    hits,
    tool_called,
    agent_reply,
    expected_verdict,
    expected_reason,
    expected_success,
):
    """Assert that /api/test/search runs the probe and classifies according to matrix."""
    # 1. Mock SearXNG probe hits count
    monkeypatch.setattr(
        "routers.test.probe_searxng",
        AsyncMock(return_value=hits),
    )

    # 2. Mock hermes_bridge stream_prompt to emit events mimicking the test scenario
    async def fake_stream_prompt(*args, **kwargs):
        yield {"type": "session", "session_id": "probe-session-123"}
        if tool_called:
            yield {"type": "tool_start", "tool": "web_search", "detail": "query"}
            yield {"type": "tool_complete", "tool": "web_search"}
        yield {
            "type": "complete",
            "session_id": "probe-session-123",
            "text": agent_reply,
            "status": "ok",
        }

    monkeypatch.setattr(
        "routers.test.hermes_bridge.stream_prompt",
        fake_stream_prompt,
    )

    # 3. Call the API and verify verdict and reason matching the matrix
    resp = test_client.get("/api/test/search", headers=test_client.auth_headers)
    assert resp.status_code == 200

    data = resp.json()
    assert data["success"] is expected_success
    assert data["verdict"] == expected_verdict
    assert data["reason"] == expected_reason
    assert data["hits"] == hits
    assert data["tool_was_called"] is tool_called
    assert data["agent_reply"] == agent_reply


def test_search_probe_handles_hermes_error(test_client, monkeypatch):
    """Verify that a failure in the Hermes stream is handled gracefully and fails the probe."""
    monkeypatch.setattr(
        "routers.test.probe_searxng",
        AsyncMock(return_value=5),
    )

    async def error_stream_prompt(*args, **kwargs):
        yield {"type": "session", "session_id": "probe-session-123"}
        raise RuntimeError("simulated hermes crash")

    monkeypatch.setattr(
        "routers.test.hermes_bridge.stream_prompt",
        error_stream_prompt,
    )

    resp = test_client.get("/api/test/search", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["verdict"] == "fail"
    assert "hermes_unavailable" in data["reason"]
