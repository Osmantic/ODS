"""Router-level integration tests for the ODS Dashboard API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch



# ---------------------------------------------------------------------------
# Health & Core
# ---------------------------------------------------------------------------


def test_health_returns_ok(test_client):
    """GET /health should return 200 with status 'ok' — no auth required."""
    resp = test_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "timestamp" in data


def test_host_agent_diagnostics_requires_auth(test_client):
    """GET /api/host-agent/diagnostics without auth returns 401."""
    resp = test_client.get("/api/host-agent/diagnostics")
    assert resp.status_code == 401


def test_host_agent_diagnostics_success(test_client, monkeypatch):
    """Host-agent diagnostics reports URL, gateway, auth, and probe state."""
    import main as main_mod

    captured = {}

    def fake_request_json(method, path, *, timeout):
        captured.update(method=method, path=path, timeout=timeout)
        return {"status": "ok", "version": "1.0.0"}

    monkeypatch.setattr(main_mod, "AGENT_HOST", "172.18.0.1")
    monkeypatch.setattr(main_mod, "AGENT_PORT", 7710)
    monkeypatch.setattr(main_mod, "ODS_AGENT_KEY", "secret")
    monkeypatch.setattr(main_mod, "_running_inside_container", lambda: True)
    monkeypatch.setattr(main_mod, "_detect_container_default_gateway", lambda: "172.18.0.1")
    monkeypatch.setattr(main_mod, "_host_agent_probe_state", {"last_success_at": None, "last_error": None})
    monkeypatch.setattr(main_mod, "request_agent_json", fake_request_json)

    resp = test_client.get("/api/host-agent/diagnostics", headers=test_client.auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"]["url"] == main_mod.AGENT_URL
    assert data["configured"]["ods_agent_key_configured"] is True
    assert data["container"]["inside_container"] is True
    assert data["container"]["default_gateway"] == "172.18.0.1"
    assert data["probe"]["available"] is True
    assert data["probe"]["status_code"] == 200
    assert data["probe"]["response"]["status"] == "ok"
    assert data["probe"]["last_success_at"]
    assert data["probe"]["last_error"] is None
    assert captured == {
        "method": "GET",
        "path": "/health",
        "timeout": 3,
    }


def test_host_agent_diagnostics_omits_gateway_outside_container(test_client, monkeypatch):
    """Host-side diagnostics should not report a host route as a container gateway."""
    import main as main_mod

    monkeypatch.setattr(main_mod, "AGENT_HOST", "127.0.0.1")
    monkeypatch.setattr(main_mod, "AGENT_PORT", 7710)
    monkeypatch.setattr(main_mod, "ODS_AGENT_KEY", "")
    monkeypatch.setattr(main_mod, "_running_inside_container", lambda: False)
    monkeypatch.setattr(
        main_mod,
        "_detect_container_default_gateway",
        lambda: (_ for _ in ()).throw(AssertionError("gateway detection should not run")),
    )
    monkeypatch.setattr(
        main_mod,
        "_probe_host_agent_health",
        lambda: {
            "available": False,
            "status_code": None,
            "response": None,
            "error": "not checked",
            "checked_at": "2026-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(main_mod, "_host_agent_probe_state", {"last_success_at": None, "last_error": None})

    resp = test_client.get("/api/host-agent/diagnostics", headers=test_client.auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["container"]["inside_container"] is False
    assert data["container"]["default_gateway"] is None


def test_host_agent_diagnostics_failure_keeps_last_success(test_client, monkeypatch):
    """A failed probe reports the error without discarding last success."""
    import main as main_mod
    from host_agent_client import AgentUnavailable

    monkeypatch.setattr(main_mod, "AGENT_HOST", "172.18.0.1")
    monkeypatch.setattr(main_mod, "AGENT_PORT", 7710)
    monkeypatch.setattr(main_mod, "ODS_AGENT_KEY", "")
    monkeypatch.setattr(main_mod, "_running_inside_container", lambda: True)
    monkeypatch.setattr(main_mod, "_detect_container_default_gateway", lambda: "172.18.0.1")
    monkeypatch.setattr(
        main_mod,
        "_host_agent_probe_state",
        {"last_success_at": "2026-01-01T00:00:00+00:00", "last_error": None},
    )

    def fake_request_json(*_args, **_kwargs):
        raise AgentUnavailable("timed out")

    monkeypatch.setattr(main_mod, "request_agent_json", fake_request_json)

    resp = test_client.get("/api/host-agent/diagnostics", headers=test_client.auth_headers)

    assert resp.status_code == 200
    probe = resp.json()["probe"]
    assert probe["available"] is False
    assert probe["status_code"] is None
    assert "timed out" in probe["error"]
    assert probe["last_success_at"] == "2026-01-01T00:00:00+00:00"
    assert "timed out" in probe["last_error"]


# ---------------------------------------------------------------------------
# Auth enforcement — no Bearer token → 401
# ---------------------------------------------------------------------------


def test_setup_status_requires_auth(test_client):
    """GET /api/setup/status without auth header → 401."""
    resp = test_client.get("/api/setup/status")
    assert resp.status_code == 401


def test_api_status_requires_auth(test_client):
    """GET /api/status without auth header → 401."""
    resp = test_client.get("/api/status")
    assert resp.status_code == 401


def test_privacy_shield_status_requires_auth(test_client):
    """GET /api/privacy-shield/status without auth header → 401."""
    resp = test_client.get("/api/privacy-shield/status")
    assert resp.status_code == 401


def test_workflows_requires_auth(test_client):
    """GET /api/workflows without auth header → 401."""
    resp = test_client.get("/api/workflows")
    assert resp.status_code == 401


def test_agents_metrics_requires_auth(test_client):
    """GET /api/agents/metrics without auth header → 401."""
    resp = test_client.get("/api/agents/metrics")
    assert resp.status_code == 401


def test_agents_cluster_requires_auth(test_client):
    """GET /api/agents/cluster without auth header → 401."""
    resp = test_client.get("/api/agents/cluster")
    assert resp.status_code == 401


def test_agents_throughput_requires_auth(test_client):
    """GET /api/agents/throughput without auth header → 401."""
    resp = test_client.get("/api/agents/throughput")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Setup router
# ---------------------------------------------------------------------------


def test_setup_status_authenticated(test_client, setup_config_dir):
    """GET /api/setup/status with auth → 200, returns first_run and personas_available."""
    resp = test_client.get("/api/setup/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "first_run" in data
    assert "personas_available" in data
    assert isinstance(data["personas_available"], list)
    assert len(data["personas_available"]) > 0


def test_setup_status_first_run_true(test_client, setup_config_dir):
    """first_run is True when setup-complete.json does not exist."""
    resp = test_client.get("/api/setup/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["first_run"] is True


def test_setup_status_first_run_false(test_client, setup_config_dir):
    """first_run is False when setup-complete.json exists."""
    (setup_config_dir / "setup-complete.json").write_text('{"completed_at": "now"}')
    resp = test_client.get("/api/setup/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["first_run"] is False


def test_setup_persona_valid(test_client, setup_config_dir):
    """POST /api/setup/persona with valid persona → 200, writes persona.json."""
    resp = test_client.post(
        "/api/setup/persona",
        json={"persona": "general"},
        headers=test_client.auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["persona"] == "general"
    persona_file = setup_config_dir / "persona.json"
    assert persona_file.exists()


def test_setup_persona_invalid(test_client, setup_config_dir):
    """POST /api/setup/persona with invalid persona → 400."""
    resp = test_client.post(
        "/api/setup/persona",
        json={"persona": "nonexistent-persona"},
        headers=test_client.auth_headers,
    )
    assert resp.status_code == 400


def test_setup_complete(test_client, setup_config_dir):
    """POST /api/setup/complete → 200, writes setup-complete.json."""
    resp = test_client.post("/api/setup/complete", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert (setup_config_dir / "setup-complete.json").exists()


def test_list_personas(test_client):
    """GET /api/setup/personas → 200, returns list with at least general/coding/creative."""
    resp = test_client.get("/api/setup/personas", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "personas" in data
    persona_ids = [p["id"] for p in data["personas"]]
    assert "general" in persona_ids
    assert "coding" in persona_ids


def test_get_persona_info_existing(test_client):
    """GET /api/setup/persona/general → 200 with persona details."""
    resp = test_client.get("/api/setup/persona/general", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "general"
    assert "name" in data
    assert "system_prompt" in data


def test_get_persona_info_nonexistent(test_client):
    """GET /api/setup/persona/nonexistent → 404."""
    resp = test_client.get("/api/setup/persona/nonexistent", headers=test_client.auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Preflight endpoints
# ---------------------------------------------------------------------------


def test_preflight_ports_empty_list(test_client):
    """POST /api/preflight/ports with empty ports list → 200, no conflicts."""
    resp = test_client.post(
        "/api/preflight/ports",
        json={"ports": []},
        headers=test_client.auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["conflicts"] == []
    assert data["available"] is True


def test_preflight_required_ports_requires_auth(test_client):
    """GET /api/preflight/required-ports without auth → 401 (gated like siblings)."""
    resp = test_client.get("/api/preflight/required-ports")
    assert resp.status_code == 401


def test_preflight_required_ports_authenticated(test_client):
    """GET /api/preflight/required-ports with auth → 200, returns port list."""
    resp = test_client.get(
        "/api/preflight/required-ports", headers=test_client.auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "ports" in data
    assert isinstance(data["ports"], list)


def test_preflight_docker_authenticated(test_client):
    """GET /api/preflight/docker with auth → 200, returns docker availability."""
    resp = test_client.get("/api/preflight/docker", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "available" in data
    if data["available"]:
        assert "version" in data


def test_preflight_gpu_authenticated(test_client):
    """GET /api/preflight/gpu with auth → 200, returns GPU info or error."""
    resp = test_client.get("/api/preflight/gpu", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "available" in data
    if data["available"]:
        assert "name" in data
        assert "vram" in data
        assert "backend" in data
    else:
        assert "error" in data


def test_preflight_disk_authenticated(test_client):
    """GET /api/preflight/disk with auth → 200, returns disk space info."""
    resp = test_client.get("/api/preflight/disk", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "free" in data
    assert "total" in data
    assert "used" in data
    assert "path" in data


# ---------------------------------------------------------------------------
# Workflow path-traversal and catalog miss
# ---------------------------------------------------------------------------


def test_workflow_enable_path_traversal(test_client):
    """POST with path-traversal chars in workflow_id → 400 (regex rejects it)."""
    resp = test_client.post(
        "/api/workflows/../../etc/passwd/enable",
        headers=test_client.auth_headers,
    )
    # FastAPI path matching will either 404 (no route match) or 400 (validation).
    # Either is acceptable — the traversal must NOT succeed (not 200).
    assert resp.status_code in (400, 404, 422)


def test_workflow_enable_unknown_id(test_client):
    """POST /api/workflows/valid-id/enable → 404 when not in catalog."""
    resp = test_client.post(
        "/api/workflows/valid-id/enable",
        headers=test_client.auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Privacy Shield (mock subprocess so docker is not required)
# ---------------------------------------------------------------------------


def test_privacy_shield_status_with_mock(test_client):
    """GET /api/privacy-shield/status → 200 with mocked docker subprocess."""

    async def _fake_create_subprocess(*args, **kwargs):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_create_subprocess):
        resp = test_client.get(
            "/api/privacy-shield/status",
            headers=test_client.auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data
    assert "container_running" in data
    assert "port" in data


# ---------------------------------------------------------------------------
# Core API Endpoints
# ---------------------------------------------------------------------------


def test_api_status_authenticated(test_client, monkeypatch):
    """GET /api/status with auth → 200, returns full system status."""
    monkeypatch.setattr(
        "main._build_api_status",
        AsyncMock(return_value={
            "gpu": None,
            "services": [],
            "model": None,
            "bootstrap": {},
            "uptime": 0,
            "version": "test",
            "tier": "Unknown",
            "cpu": {},
            "ram": {},
            "inference": {},
        }),
    )
    resp = test_client.get("/api/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "gpu" in data
    assert "services" in data
    assert "model" in data
    assert "bootstrap" in data
    assert "uptime" in data
    assert "version" in data
    assert "tier" in data
    assert "cpu" in data
    assert "ram" in data
    assert "inference" in data


def test_api_storage_authenticated(test_client):
    """GET /api/storage with auth → 200, returns storage breakdown."""
    resp = test_client.get("/api/storage", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "vector_db" in data
    assert "total_data" in data
    assert "disk" in data
    assert "gb" in data["models"]
    assert "percent" in data["models"]


def test_api_external_links_authenticated(test_client):
    """GET /api/external-links with auth → 200, returns sidebar links."""
    resp = test_client.get("/api/external-links", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    for link in data:
        assert "id" in link
        assert "label" in link
        assert "port" in link
        assert "icon" in link


def test_api_service_tokens_authenticated(test_client):
    """GET /api/service-tokens with auth → 200, returns service tokens."""
    resp = test_client.get("/api/service-tokens", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Agents router
# ---------------------------------------------------------------------------


def test_agents_metrics_authenticated(test_client):
    """GET /api/agents/metrics with auth → 200, returns agent metrics with seeded data."""
    from agent_monitor import agent_metrics, throughput

    # Reset singletons to avoid cross-test contamination
    throughput.data_points = []
    agent_metrics.session_count = 0
    agent_metrics.tokens_per_second = 0.0

    # Seed non-default values to test actual aggregation
    agent_metrics.session_count = 5
    agent_metrics.tokens_per_second = 123.45
    throughput.add_sample(100.0)
    throughput.add_sample(150.0)

    resp = test_client.get("/api/agents/metrics", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "agent" in data
    assert "cluster" in data
    assert "throughput" in data

    # Verify seeded values are reflected in response
    assert data["agent"]["session_count"] == 5
    assert data["agent"]["tokens_per_second"] == 123.45
    assert data["throughput"]["current"] == 150.0
    assert data["throughput"]["peak"] == 150.0


def test_agents_cluster_authenticated(test_client):
    """GET /api/agents/cluster with auth → 200, returns cluster status with mocked data."""

    async def _fake_subprocess(*args, **kwargs):
        """Mock subprocess that returns a 2-node cluster with 1 healthy node."""
        proc = MagicMock()
        cluster_response = b'{"nodes": [{"id": "node1", "healthy": true}, {"id": "node2", "healthy": false}]}'
        proc.communicate = AsyncMock(return_value=(cluster_response, b""))
        proc.returncode = 0
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_subprocess):
        resp = test_client.get("/api/agents/cluster", headers=test_client.auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data
    assert "total_gpus" in data
    assert "active_gpus" in data
    assert "failover_ready" in data

    # Verify parsed cluster data
    assert data["total_gpus"] == 2
    assert data["active_gpus"] == 1
    assert data["failover_ready"] is False  # Only 1 healthy node, need >1 for failover


def test_agents_cluster_failover_ready(test_client):
    """GET /api/agents/cluster with 2 healthy nodes → failover_ready is True."""

    async def _fake_subprocess(*args, **kwargs):
        """Mock subprocess that returns a 2-node cluster with both nodes healthy."""
        proc = MagicMock()
        cluster_response = b'{"nodes": [{"id": "node1", "healthy": true}, {"id": "node2", "healthy": true}]}'
        proc.communicate = AsyncMock(return_value=(cluster_response, b""))
        proc.returncode = 0
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_subprocess):
        resp = test_client.get("/api/agents/cluster", headers=test_client.auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_gpus"] == 2
    assert data["active_gpus"] == 2
    assert data["failover_ready"] is True  # 2 healthy nodes enables failover


def test_agents_metrics_html_xss_escaping(test_client):
    """GET /api/agents/metrics.html escapes HTML special chars to prevent XSS."""
    from agent_monitor import agent_metrics, throughput

    # Reset singletons to avoid cross-test contamination
    throughput.data_points = []
    agent_metrics.session_count = 0

    # Inject XSS payload into agent metrics
    agent_metrics.session_count = 999
    throughput.add_sample(42.0)

    # Mock cluster status with XSS payload in node data
    async def _fake_subprocess(*args, **kwargs):
        proc = MagicMock()
        # Node ID contains script tag
        cluster_response = b'{"nodes": [{"id": "<script>alert(1)</script>", "healthy": true}]}'
        proc.communicate = AsyncMock(return_value=(cluster_response, b""))
        proc.returncode = 0
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_subprocess):
        resp = test_client.get("/api/agents/metrics.html", headers=test_client.auth_headers)

    assert resp.status_code == 200
    html_content = resp.text

    # Verify HTML special chars are escaped
    assert "<script>" not in html_content
    assert "&lt;script&gt;" in html_content or "alert(1)" not in html_content
    # Verify legitimate content is present
    assert "999" in html_content  # session_count
    assert "42.0" in html_content  # throughput


def test_agents_throughput_authenticated(test_client):
    """GET /api/agents/throughput with auth → 200, returns throughput stats with real data."""
    from agent_monitor import throughput

    # Reset singleton to avoid cross-test contamination
    throughput.data_points = []

    # Seed throughput data to test actual behavior
    throughput.add_sample(42.0)
    throughput.add_sample(55.0)
    throughput.add_sample(38.0)

    resp = test_client.get("/api/agents/throughput", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "current" in data
    assert "average" in data
    assert "peak" in data
    assert "history" in data

    # Verify calculated stats
    assert data["current"] == 38.0  # Last sample
    assert data["peak"] == 55.0  # Max of all samples
    assert data["average"] == (42.0 + 55.0 + 38.0) / 3  # Average of all samples
    assert len(data["history"]) == 3


# ---------------------------------------------------------------------------
# Setup router — get_active_persona_prompt
# ---------------------------------------------------------------------------


def test_get_active_persona_prompt_with_persona(setup_config_dir):
    """get_active_persona_prompt reads system_prompt from persona.json."""
    import json
    from routers.setup import get_active_persona_prompt
    persona_file = setup_config_dir / "persona.json"
    persona_file.write_text(json.dumps({"system_prompt": "custom prompt"}))
    assert get_active_persona_prompt() == "custom prompt"


def test_get_active_persona_prompt_defaults_when_no_file(setup_config_dir):
    """get_active_persona_prompt returns general prompt when no persona.json."""
    from routers.setup import get_active_persona_prompt
    from config import PERSONAS
    assert get_active_persona_prompt() == PERSONAS["general"]["system_prompt"]


def test_get_active_persona_prompt_corrupt_file(setup_config_dir):
    """get_active_persona_prompt returns default on corrupt JSON."""
    from routers.setup import get_active_persona_prompt
    from config import PERSONAS
    persona_file = setup_config_dir / "persona.json"
    persona_file.write_text("not valid json{{{")
    assert get_active_persona_prompt() == PERSONAS["general"]["system_prompt"]


# ---------------------------------------------------------------------------
# Setup router — setup_status with progress + persona
# ---------------------------------------------------------------------------


def test_setup_status_with_progress_file(test_client, setup_config_dir):
    """GET /api/setup/status reads step from setup-progress.json."""
    import json
    (setup_config_dir / "setup-progress.json").write_text(json.dumps({"step": 3}))
    resp = test_client.get("/api/setup/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["step"] == 3


def test_setup_status_with_persona_file(test_client, setup_config_dir):
    """GET /api/setup/status reads persona from persona.json."""
    import json
    (setup_config_dir / "persona.json").write_text(json.dumps({"persona": "coding"}))
    resp = test_client.get("/api/setup/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["persona"] == "coding"


def test_setup_status_corrupt_progress_file(test_client, setup_config_dir):
    """GET /api/setup/status with corrupt progress file → step defaults to 0."""
    (setup_config_dir / "setup-progress.json").write_text("not json{{{")
    resp = test_client.get("/api/setup/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["step"] == 0


def test_setup_status_corrupt_persona_file(test_client, setup_config_dir):
    """GET /api/setup/status with corrupt persona file → persona is None."""
    (setup_config_dir / "persona.json").write_text("bad json")
    resp = test_client.get("/api/setup/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["persona"] is None


# ---------------------------------------------------------------------------
# Setup router — /api/setup/complete deletes progress file
# ---------------------------------------------------------------------------


def test_setup_complete_removes_progress(test_client, setup_config_dir):
    """POST /api/setup/complete deletes setup-progress.json if it exists."""
    import json
    (setup_config_dir / "setup-progress.json").write_text(json.dumps({"step": 2}))
    resp = test_client.post("/api/setup/complete", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert not (setup_config_dir / "setup-progress.json").exists()


# ---------------------------------------------------------------------------
# Setup router — /api/setup/test (script not found fallback)
# ---------------------------------------------------------------------------


def test_setup_test_no_script_fallback(test_client, monkeypatch):
    """POST /api/setup/test when script not found → streaming connectivity test."""
    import routers.setup as setup_mod

    monkeypatch.setattr(setup_mod, "INSTALL_DIR", "/tmp/nonexistent-ods")

    resp = test_client.post("/api/setup/test", headers=test_client.auth_headers)
    assert resp.status_code == 200
    # StreamingResponse returns text/plain
    assert "text/plain" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Setup router — /api/chat
# ---------------------------------------------------------------------------


def test_chat_success(test_client, monkeypatch):
    """POST /api/chat with mocked LLM → 200, returns response."""
    import routers.setup as setup_router

    monkeypatch.setattr(
        setup_router,
        "read_live_env_value",
        lambda key, default="": "new-live-model" if key == "LLM_MODEL" else default,
    )
    resp_mock = AsyncMock()
    resp_mock.status = 200
    resp_mock.json = AsyncMock(return_value={
        "choices": [{"message": {"content": "Hello from the LLM!"}}]
    })

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)

    session_mock = MagicMock()
    session_mock.post = MagicMock(return_value=ctx)
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session_mock)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("routers.setup.aiohttp.ClientSession", return_value=session_ctx):
        resp = test_client.post(
            "/api/chat",
            json={"message": "hi", "system": "You are helpful"},
            headers=test_client.auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["response"] == "Hello from the LLM!"
    assert session_mock.post.call_args.kwargs["json"]["model"] == "new-live-model"


def test_chat_llm_error(test_client, monkeypatch):
    """POST /api/chat when LLM returns non-200 → HTTPException."""
    resp_mock = AsyncMock()
    resp_mock.status = 500
    resp_mock.text = AsyncMock(return_value="internal error")

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)

    session_mock = MagicMock()
    session_mock.post = MagicMock(return_value=ctx)
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session_mock)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("routers.setup.aiohttp.ClientSession", return_value=session_ctx):
        resp = test_client.post(
            "/api/chat",
            json={"message": "hi"},
            headers=test_client.auth_headers,
        )

    assert resp.status_code == 500


def test_chat_connection_error(test_client, monkeypatch):
    """POST /api/chat when LLM is unreachable → 503."""
    import aiohttp

    session_mock = MagicMock()
    session_mock.post = MagicMock(side_effect=aiohttp.ClientError("refused"))
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session_mock)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("routers.setup.aiohttp.ClientSession", return_value=session_ctx):
        resp = test_client.post(
            "/api/chat",
            json={"message": "hi"},
            headers=test_client.auth_headers,
        )

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Models router — split-file download status (issue #316)
# ---------------------------------------------------------------------------


def _models_get(test_client) -> dict:
    resp = test_client.get("/api/models", headers=test_client.auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _patch_models_env(monkeypatch, library, downloaded):
    """Patch routers.models helpers used by list_models."""
    import routers.models as models_router
    monkeypatch.setattr(models_router, "_load_library", lambda: library)
    monkeypatch.setattr(models_router, "_scan_downloaded_models", lambda: downloaded)
    monkeypatch.setattr(models_router, "_read_active_model", lambda: None)
    monkeypatch.setattr(models_router, "_get_gpu_vram", lambda: None)


def test_list_models_split_partial_not_downloaded(test_client, monkeypatch):
    """A split-file model with only the first part on disk → status 'available'."""
    library = [{
        "id": "split-test",
        "name": "Split Test",
        "gguf_file": "split-test-00001-of-00002.gguf",
        "gguf_parts": [
            {"file": "split-test-00001-of-00002.gguf"},
            {"file": "split-test-00002-of-00002.gguf"},
        ],
        "size_mb": 2048,
        "vram_required_gb": 8,
    }]
    downloaded = {"split-test-00001-of-00002.gguf": 1024}
    _patch_models_env(monkeypatch, library, downloaded)

    data = _models_get(test_client)
    assert len(data["models"]) == 1
    assert data["models"][0]["status"] == "available"


def test_list_models_split_all_parts_downloaded(test_client, monkeypatch):
    """A split-file model with every part on disk → status 'downloaded'."""
    library = [{
        "id": "split-test",
        "name": "Split Test",
        "gguf_file": "split-test-00001-of-00002.gguf",
        "gguf_parts": [
            {"file": "split-test-00001-of-00002.gguf"},
            {"file": "split-test-00002-of-00002.gguf"},
        ],
        "size_mb": 2048,
        "vram_required_gb": 8,
    }]
    downloaded = {
        "split-test-00001-of-00002.gguf": 1024,
        "split-test-00002-of-00002.gguf": 1024,
    }
    _patch_models_env(monkeypatch, library, downloaded)

    data = _models_get(test_client)
    assert data["models"][0]["status"] == "downloaded"


def test_list_models_single_file_downloaded(test_client, monkeypatch):
    """Sanity check: single-file model with its gguf_file present → 'downloaded'."""
    library = [{
        "id": "single-test",
        "name": "Single Test",
        "gguf_file": "single-test.gguf",
        "size_mb": 1024,
        "vram_required_gb": 4,
    }]
    downloaded = {"single-test.gguf": 1024}
    _patch_models_env(monkeypatch, library, downloaded)

    data = _models_get(test_client)
    assert data["models"][0]["status"] == "downloaded"

