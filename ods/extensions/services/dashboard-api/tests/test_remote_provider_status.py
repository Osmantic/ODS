"""Tests for dashboard-api remote-provider status."""

from __future__ import annotations

import asyncio
import json


def _route_state(
    model: str = "qwen/remote:latest",
    *,
    peer: dict[str, object] | None = None,
    status: dict[str, object] | None = None,
    transport: str = "direct",
) -> dict[str, object]:
    state = {
        "schema": "ods.remote-routing-state.v1",
        "enabled": True,
        "mode": "cloud",
        "provider": {
            "capability": "openai-compatible",
            "baseUrl": "https://gpu.example.test/v1",
            "model": model,
            "transport": transport,
        },
        "projection": {
            "publicModel": "ods/current",
            "gateway": "litellm-cloud",
            "egressBaseUrl": "http://remote-provider-egress:8091/v1",
            "consumerRoute": "gateway",
        },
        "status": status or {"proven": False, "reason": "pending-provider-handshake"},
    }
    if peer is not None:
        state["peer"] = peer
    return state


def _lifecycle_payload() -> dict[str, object]:
    return {
        "action": "test",
        "provider": {
            "transport": "direct",
            "baseUrl": "https://gpu.example.test",
            "model": "qwen/remote:latest",
        },
        "secrets": {"apiKey": "unit-test-provider-token"},
    }


def _patch_state_path(monkeypatch, path):
    from routers import remote_provider_status as rps

    monkeypatch.setattr(rps, "_state_path", lambda: path)
    activation_path = path.with_name("activation-public.json")
    monkeypatch.setattr(rps, "_activation_path", lambda: activation_path)
    async def verified_activation(value):
        return value

    monkeypatch.setattr(rps, "_reconcile_activation_with_host", verified_activation)
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            state = {}
        provider = state.get("provider") if isinstance(state.get("provider"), dict) else {}
        if state.get("enabled") is True:
            activation_path.write_text(
                json.dumps({
                    "schema": "ods.remote-provider-activation-state.v1",
                    "active": True,
                    "proven": True,
                    "gateway": "litellm-cloud",
                    "publicModel": "ods/current",
                    "model": provider.get("model", "qwen/remote:latest"),
                    "routeFingerprint": rps._route_fingerprint(state),
                    "contextLength": provider.get("contextLength", 32768),
                    "maxTokens": provider.get("maxTokens", 4096),
                    "reasoning": provider.get("reasoning", False),
                    "pixel": "reconciled",
                    "updatedAt": "2026-07-26T00:00:00+00:00",
                }),
                encoding="utf-8",
            )
    return rps


def test_remote_provider_status_requires_auth(test_client):
    resp = test_client.get("/api/remote-provider/status")
    assert resp.status_code == 401


def test_overall_status_requires_a_proven_consumer_activation():
    from routers import remote_provider_status as rps

    route = {
        "valid": True,
        "enabled": True,
        "provider": {
            "transport": "direct",
            "baseUrl": "https://gpu.example.test/v1",
            "model": "qwen/remote:latest",
            "contextLength": 32768,
            "maxTokens": 4096,
            "reasoning": False,
        },
    }
    egress = {"reachable": True, "ready": True}
    assert rps._overall_status(
        route,
        egress,
        {"valid": True, "active": False, "proven": False},
    ) == "degraded"
    assert rps._overall_status(
        route,
        egress,
        {
            "valid": True,
            "active": True,
            "proven": True,
            "routeFingerprint": rps._route_fingerprint(route),
        },
    ) == "ready"
    assert rps._overall_status(
        route,
        egress,
        {
            "valid": True,
            "active": True,
            "proven": True,
            "routeFingerprint": "0" * 64,
        },
    ) == "degraded"


def test_activation_status_rejects_incomplete_ready_claim(monkeypatch, tmp_path):
    from routers import remote_provider_status as rps

    path = tmp_path / "activation-public.json"
    path.write_text(
        json.dumps(
            {
                "schema": "ods.remote-provider-activation-state.v1",
                "active": True,
                "proven": True,
                "gateway": "litellm-cloud",
                "publicModel": "ods/current",
                "model": "org/qwen:remote",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(rps, "_activation_path", lambda: path)

    assert rps._read_activation() == {
        "valid": False,
        "active": True,
        "proven": False,
        "reason": "invalid_activation_contract",
    }


def test_activation_status_treats_missing_receipt_as_not_activated(monkeypatch, tmp_path):
    from routers import remote_provider_status as rps

    monkeypatch.setattr(rps, "_activation_path", lambda: tmp_path / "missing.json")

    assert rps._read_activation() == {
        "valid": True,
        "active": False,
        "proven": False,
        "reason": "not_activated",
    }


def test_activation_reconciliation_rejects_current_host_consumer_drift(monkeypatch):
    from routers import remote_provider_status as rps

    activation = {
        "valid": True,
        "active": True,
        "proven": True,
        "reason": "active_and_proven",
        "gateway": "litellm-cloud",
        "publicModel": "ods/current",
        "model": "remote-owner-model",
        "routeFingerprint": "e" * 64,
        "contextLength": 131072,
        "maxTokens": 16384,
        "reasoning": False,
        "pixel": "reconciled",
        "updatedAt": "2026-08-31T18:05:36Z",
    }

    async def local_runtime(*_args, **_kwargs):
        return {
            "status": "idle",
            "activeAgentViable": False,
            "activeRuntime": None,
        }

    monkeypatch.setattr(rps, "async_request_agent_json", local_runtime)
    result = asyncio.run(rps._reconcile_activation_with_host(activation))

    assert result["active"] is True
    assert result["valid"] is False
    assert result["proven"] is False
    assert result["reason"] == "consumer_drift"
    assert result["pixel"] == "drifted"


def test_activation_reconciliation_accepts_exact_remote_host_runtime(monkeypatch):
    from routers import remote_provider_status as rps

    activation = {
        "valid": True,
        "active": True,
        "proven": True,
        "reason": "active_and_proven",
        "model": "remote-owner-model",
        "contextLength": 131072,
        "maxTokens": 16384,
        "reasoning": False,
        "pixel": "reconciled",
    }

    async def remote_runtime(*_args, **_kwargs):
        return {
            "status": "idle",
            "activeAgentViable": True,
            "activeRuntime": {
                "source": "remote-provider",
                "model": "remote-owner-model",
                "contextLength": 131072,
                "maxTokens": 16384,
                "reasoning": False,
            },
        }

    monkeypatch.setattr(rps, "async_request_agent_json", remote_runtime)
    result = asyncio.run(rps._reconcile_activation_with_host(activation))

    assert result == activation


def test_activation_reconciliation_fails_closed_when_host_status_is_unavailable(monkeypatch):
    from host_agent_client import AgentUnavailable
    from routers import remote_provider_status as rps

    activation = {
        "valid": True,
        "active": True,
        "proven": True,
        "reason": "active_and_proven",
        "model": "remote-owner-model",
        "contextLength": 131072,
        "maxTokens": 16384,
        "reasoning": False,
        "pixel": "reconciled",
    }

    async def unavailable_runtime(*_args, **_kwargs):
        raise AgentUnavailable("host agent is unavailable")

    monkeypatch.setattr(rps, "async_request_agent_json", unavailable_runtime)
    result = asyncio.run(rps._reconcile_activation_with_host(activation))

    assert result["active"] is True
    assert result["valid"] is False
    assert result["proven"] is False
    assert result["reason"] == "consumer_status_unavailable"
    assert result["pixel"] == "unverified"


def test_remote_provider_status_missing_state_is_disabled(
    test_client,
    monkeypatch,
    tmp_path,
):
    rps = _patch_state_path(monkeypatch, tmp_path / "missing.json")

    async def fake_fetch():
        return {
            "reachable": True,
            "valid": True,
            "ready": False,
            "status": "disabled",
            "reason": "remote_route_disabled",
            "secret": {"configured": False, "bytes": 0},
            "resolution": None,
        }

    monkeypatch.setattr(rps, "_fetch_egress_health", fake_fetch)
    async def fake_ssh_supervisor():
        return rps._safe_ssh_supervisor_status(
            {
                "schema": "ods.remote-provider-ssh-supervisor-plan.v1",
                "status": "disabled",
                "ready": False,
                "readyToStart": False,
                "reason": "remote_route_disabled",
                "tunnelBaseUrl": None,
                "tunnels": [],
                "secrets": {},
                "missingSecrets": [],
            }
        )

    monkeypatch.setattr(rps, "_fetch_ssh_supervisor_status", fake_ssh_supervisor)

    resp = test_client.get(
        "/api/remote-provider/status",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "disabled"
    assert body["routeState"]["exists"] is False
    assert body["routeState"]["valid"] is True
    assert body["routeState"]["provider"] is None
    assert body["availableActions"]["configure"] is True
    assert body["availableActions"]["test"] is False
    assert body["availableActions"]["enable"] is False
    assert body["sshSupervisor"]["status"] == "disabled"


def test_disabled_route_exposes_only_saved_profile_availability(monkeypatch, tmp_path):
    from routers import remote_provider_status as rps

    state_path = tmp_path / "routing-state.json"
    state_path.write_text(
        json.dumps({
            "schema": "ods.remote-routing-state.v1",
            "enabled": False,
            "mode": "cloud",
            "provider": None,
            "ssh": None,
            "peer": None,
            "resume": {
                "available": True,
                "profileSha256": "a" * 64,
                "savedAt": "2026-08-31T19:00:00+00:00",
                "baseUrl": "https://must-not-leak.example.test/v1",
            },
            "status": {"proven": False, "reason": "disabled"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(rps, "_state_path", lambda: state_path)

    state = rps._read_route_state()

    assert state["valid"] is True
    assert state["enabled"] is False
    assert state["resumeAvailable"] is True
    assert state["provider"] is None
    assert "resume" not in state
    assert "must-not-leak" not in json.dumps(state)


def test_remote_provider_status_sanitizes_egress_secret_health(
    test_client,
    monkeypatch,
    tmp_path,
):
    state_path = tmp_path / "routing-state.json"
    state_path.write_text(json.dumps(_route_state()), encoding="utf-8")
    rps = _patch_state_path(monkeypatch, state_path)

    async def fake_fetch():
        return rps._sanitize_egress_health(
            {
                "status": "ok",
                "ready": True,
                "reason": "ready",
                "secret": {
                    "configured": True,
                    "bytes": 24,
                    "path": "/state/remote-provider/secrets/provider-api-key",
                    "value": "unit-test-provider-token",
                },
                "resolution": {"ok": True, "addressCount": 1},
            }
        )

    monkeypatch.setattr(rps, "_fetch_egress_health", fake_fetch)
    async def fake_ssh_supervisor():
        return rps._safe_ssh_supervisor_status(
            {
                "schema": "ods.remote-provider-ssh-supervisor-plan.v1",
                "status": "inactive",
                "ready": False,
                "readyToStart": False,
                "reason": "not_ssh_transport",
                "tunnelBaseUrl": None,
                "tunnels": [],
                "secrets": {},
                "missingSecrets": [],
            }
        )

    monkeypatch.setattr(rps, "_fetch_ssh_supervisor_status", fake_ssh_supervisor)

    resp = test_client.get(
        "/api/remote-provider/status",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    dumped = json.dumps(body, sort_keys=True)
    assert body["status"] == "ready"
    assert body["routeState"]["enabled"] is True
    assert body["routeState"]["provider"]["model"] == "qwen/remote:latest"
    assert body["egress"]["secret"] == {"configured": True, "bytes": 24}
    assert body["egress"]["resolution"] == {
        "ok": True,
        "reason": "",
        "addressCount": 1,
    }
    assert "unit-test-provider-token" not in dumped
    assert "provider-api-key" not in dumped
    assert body["sshSupervisor"]["status"] == "inactive"
    assert body["capabilities"]["odsPeerLifecycle"] is False


def test_remote_provider_status_reports_peer_lifecycle_readiness(
    test_client,
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "remote-provider"
    secret_dir = state_root / "secrets"
    secret_dir.mkdir(parents=True)
    secret_bytes = b"unit-test-peer-token\n"
    (secret_dir / "peer-token").write_bytes(secret_bytes)
    state_path = state_root / "routing-state.json"
    state_path.write_text(
        json.dumps(
            _route_state(
                peer={
                    "controlBaseUrl": "https://peer.example.test",
                    "transport": "direct",
                    "secretValue": "unit-test-peer-token",
                    "tokenPath": "/state/remote-provider/secrets/peer-token",
                }
            )
        ),
        encoding="utf-8",
    )
    rps = _patch_state_path(monkeypatch, state_path)
    monkeypatch.setattr(rps, "DATA_DIR", str(tmp_path))

    async def fake_fetch():
        return {
            "reachable": True,
            "valid": True,
            "ready": True,
            "status": "ok",
            "reason": "ready",
            "secret": {"configured": True, "bytes": 24},
            "resolution": {"ok": True, "addressCount": 1},
        }

    async def fake_ssh_supervisor():
        return rps._safe_ssh_supervisor_status(
            {
                "schema": "ods.remote-provider-ssh-supervisor-plan.v1",
                "status": "inactive",
                "ready": False,
                "readyToStart": False,
                "reason": "not_ssh_transport",
                "tunnelBaseUrl": None,
                "tunnels": [],
                "secrets": {},
                "missingSecrets": [],
            }
        )

    monkeypatch.setattr(rps, "_fetch_egress_health", fake_fetch)
    monkeypatch.setattr(rps, "_fetch_ssh_supervisor_status", fake_ssh_supervisor)

    resp = test_client.get(
        "/api/remote-provider/status",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    dumped = json.dumps(body, sort_keys=True)
    assert body["peer"] == {
        "configured": True,
        "ready": True,
        "reason": "ready",
        "controlBaseUrl": "https://peer.example.test",
        "transport": "direct",
        "token": {"configured": True, "bytes": len(secret_bytes)},
    }
    assert body["routeState"]["peer"] == {
        "controlBaseUrl": "https://peer.example.test",
        "transport": "direct",
    }
    assert body["capabilities"]["odsPeerLifecycle"] is True
    assert "unit-test-peer-token" not in dumped
    assert "peer-token" not in dumped


def test_remote_provider_status_blocks_peer_lifecycle_without_token(
    test_client,
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "remote-provider"
    state_root.mkdir(parents=True)
    state_path = state_root / "routing-state.json"
    state_path.write_text(
        json.dumps(
            _route_state(
                peer={
                    "controlBaseUrl": "https://peer.example.test",
                    "transport": "direct",
                }
            )
        ),
        encoding="utf-8",
    )
    rps = _patch_state_path(monkeypatch, state_path)
    monkeypatch.setattr(rps, "DATA_DIR", str(tmp_path))

    async def fake_fetch():
        return {
            "reachable": True,
            "valid": True,
            "ready": True,
            "status": "ok",
            "reason": "ready",
            "secret": {"configured": True, "bytes": 24},
            "resolution": {"ok": True, "addressCount": 1},
        }

    async def fake_ssh_supervisor():
        return rps._safe_ssh_supervisor_status(
            {
                "schema": "ods.remote-provider-ssh-supervisor-plan.v1",
                "status": "inactive",
                "ready": False,
                "readyToStart": False,
                "reason": "not_ssh_transport",
                "tunnelBaseUrl": None,
                "tunnels": [],
                "secrets": {},
                "missingSecrets": [],
            }
        )

    monkeypatch.setattr(rps, "_fetch_egress_health", fake_fetch)
    monkeypatch.setattr(rps, "_fetch_ssh_supervisor_status", fake_ssh_supervisor)

    resp = test_client.get(
        "/api/remote-provider/status",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["peer"] == {
        "configured": True,
        "ready": False,
        "reason": "missing_peer_token",
        "controlBaseUrl": "https://peer.example.test",
        "transport": "direct",
        "token": {"configured": False, "bytes": 0},
    }
    assert body["capabilities"]["odsPeerLifecycle"] is False


def test_remote_provider_peer_models_proxies_with_redacted_peer_token(
    test_client,
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "remote-provider"
    secret_dir = state_root / "secrets"
    secret_dir.mkdir(parents=True)
    (secret_dir / "peer-token").write_bytes(b"unit-test-peer-token\n")
    state_path = state_root / "routing-state.json"
    state_path.write_text(
        json.dumps(
            _route_state(
                peer={
                    "controlBaseUrl": "https://peer.example.test",
                    "transport": "direct",
                }
            )
        ),
        encoding="utf-8",
    )
    rps = _patch_state_path(monkeypatch, state_path)
    monkeypatch.setattr(rps, "DATA_DIR", str(tmp_path))
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b'{"models":[{"id":"remote-qwen"}],"echo":"unit-test-peer-token"}'

        def json(self):
            return {"models": [{"id": "remote-qwen"}], "echo": "unit-test-peer-token"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def request(self, method, url, headers):
            captured.update({"method": method, "url": url, "headers": headers})
            return FakeResponse()

    monkeypatch.setattr(rps.httpx, "AsyncClient", FakeAsyncClient)

    resp = test_client.get(
        "/api/remote-provider/peer/models",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    dumped = json.dumps(body, sort_keys=True)
    assert body == {"models": [{"id": "remote-qwen"}], "echo": "[REDACTED]"}
    assert captured["method"] == "GET"
    assert captured["url"] == "https://peer.example.test/api/models"
    assert captured["headers"]["Authorization"] == "Bearer unit-test-peer-token"
    assert captured["timeout"] == rps.PEER_PROXY_TIMEOUT_SECONDS
    assert "unit-test-peer-token" not in dumped


def test_remote_provider_peer_model_load_uses_long_timeout_and_encoded_model_id(
    test_client,
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "remote-provider"
    secret_dir = state_root / "secrets"
    secret_dir.mkdir(parents=True)
    (secret_dir / "peer-token").write_bytes(b"unit-test-peer-token\n")
    state_path = state_root / "routing-state.json"
    state_path.write_text(
        json.dumps(
            _route_state(
                peer={
                    "controlBaseUrl": "https://peer.example.test",
                    "transport": "direct",
                }
            )
        ),
        encoding="utf-8",
    )
    rps = _patch_state_path(monkeypatch, state_path)
    monkeypatch.setattr(rps, "DATA_DIR", str(tmp_path))
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b'{"status":"activated"}'

        def json(self):
            return {"status": "activated"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def request(self, method, url, headers):
            captured.update({"method": method, "url": url, "headers": headers})
            return FakeResponse()

    monkeypatch.setattr(rps.httpx, "AsyncClient", FakeAsyncClient)

    resp = test_client.post(
        "/api/remote-provider/peer/models/Qwen%203.5-9B/load",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "activated"}
    assert captured["method"] == "POST"
    assert captured["url"] == "https://peer.example.test/api/models/Qwen%203.5-9B/load"
    assert captured["timeout"] == rps.PEER_PROXY_LOAD_TIMEOUT_SECONDS


def test_remote_provider_peer_models_fail_closed_without_peer_token(
    test_client,
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "remote-provider"
    state_root.mkdir(parents=True)
    state_path = state_root / "routing-state.json"
    state_path.write_text(
        json.dumps(
            _route_state(
                peer={
                    "controlBaseUrl": "https://peer.example.test",
                    "transport": "direct",
                }
            )
        ),
        encoding="utf-8",
    )
    rps = _patch_state_path(monkeypatch, state_path)
    monkeypatch.setattr(rps, "DATA_DIR", str(tmp_path))

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("missing peer token should prevent proxy request")

    monkeypatch.setattr(rps.httpx, "AsyncClient", FakeAsyncClient)

    resp = test_client.get(
        "/api/remote-provider/peer/models",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "missing_peer_token"


def test_remote_provider_peer_models_reject_unsafe_peer_url(
    test_client,
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "remote-provider"
    secret_dir = state_root / "secrets"
    secret_dir.mkdir(parents=True)
    (secret_dir / "peer-token").write_bytes(b"unit-test-peer-token\n")
    state_path = state_root / "routing-state.json"
    state_path.write_text(
        json.dumps(
            _route_state(
                peer={
                    "controlBaseUrl": "http://127.0.0.1:8091",
                    "transport": "direct",
                }
            )
        ),
        encoding="utf-8",
    )
    rps = _patch_state_path(monkeypatch, state_path)
    monkeypatch.setattr(rps, "DATA_DIR", str(tmp_path))

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("unsafe peer URL should prevent proxy request")

    monkeypatch.setattr(rps.httpx, "AsyncClient", FakeAsyncClient)

    resp = test_client.get(
        "/api/remote-provider/peer/models",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "invalid_peer_url"


def test_remote_provider_peer_models_require_running_ssh_control_tunnel(
    test_client,
    monkeypatch,
    tmp_path,
):
    state_root = tmp_path / "remote-provider"
    secret_dir = state_root / "secrets"
    secret_dir.mkdir(parents=True)
    (secret_dir / "peer-token").write_bytes(b"unit-test-peer-token\n")
    state_path = state_root / "routing-state.json"
    state_path.write_text(
        json.dumps(
            _route_state(
                peer={
                    "controlBaseUrl": "http://remote-provider-ssh-tunnel:18092",
                    "transport": "ssh",
                },
                transport="ssh",
            )
        ),
        encoding="utf-8",
    )
    rps = _patch_state_path(monkeypatch, state_path)
    monkeypatch.setattr(rps, "DATA_DIR", str(tmp_path))

    async def fake_ssh_supervisor():
        return rps._safe_ssh_supervisor_status(
            {
                "schema": "ods.remote-provider-ssh-supervisor-plan.v1",
                "status": "planned",
                "ready": False,
                "readyToStart": True,
                "reason": "tunnel_process_not_started",
                "tunnelBaseUrl": "http://remote-provider-ssh-tunnel:18091/v1",
                "tunnels": [],
                "secrets": {},
                "missingSecrets": [],
            }
        )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("stopped SSH tunnel should prevent proxy request")

    monkeypatch.setattr(rps, "_fetch_ssh_supervisor_status", fake_ssh_supervisor)
    monkeypatch.setattr(rps.httpx, "AsyncClient", FakeAsyncClient)

    resp = test_client.get(
        "/api/remote-provider/peer/models",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "ssh_control_tunnel_not_ready"


def test_remote_provider_status_exposes_sanitized_egress_tunnel_health(
    test_client,
    monkeypatch,
    tmp_path,
):
    state_path = tmp_path / "routing-state.json"
    state_path.write_text(json.dumps(_route_state(transport="ssh")), encoding="utf-8")
    rps = _patch_state_path(monkeypatch, state_path)

    async def fake_fetch():
        return rps._sanitize_egress_health(
            {
                "status": "ok",
                "ready": True,
                "reason": "ready",
                "secret": {
                    "configured": True,
                    "bytes": 24,
                    "path": "/state/remote-provider/secrets/provider-api-key",
                    "value": "unit-test-provider-token",
                },
                "resolution": {"ok": True, "addressCount": 0, "raw": "127.0.0.1"},
                "tunnel": {
                    "ok": True,
                    "ready": True,
                    "status": "running",
                    "reason": "ready",
                    "process": {
                        "status": "running",
                        "pid": 1234,
                        "argv": ["ssh", "-i", "/state/remote-provider/secrets/ssh-identity"],
                    },
                    "secretValue": "unit-test-ssh-key",
                },
            }
        )

    async def fake_ssh_supervisor():
        return rps._safe_ssh_supervisor_status(
            {
                "schema": "ods.remote-provider-ssh-supervisor-plan.v1",
                "status": "running",
                "ready": True,
                "readyToStart": True,
                "reason": "ready",
                "tunnelBaseUrl": "http://remote-provider-ssh-tunnel:18091/v1",
                "tunnels": [],
                "secrets": {},
                "missingSecrets": [],
            }
        )

    monkeypatch.setattr(rps, "_fetch_egress_health", fake_fetch)
    monkeypatch.setattr(rps, "_fetch_ssh_supervisor_status", fake_ssh_supervisor)

    resp = test_client.get(
        "/api/remote-provider/status",
        headers=test_client.auth_headers,
    )

    body = resp.json()
    dumped = json.dumps(body, sort_keys=True)
    assert resp.status_code == 200
    assert body["status"] == "ready"
    assert body["egress"]["tunnel"] == {
        "ok": True,
        "ready": True,
        "status": "running",
        "reason": "ready",
        "process": {"status": "running", "pid": 1234},
    }
    assert body["egress"]["resolution"] == {
        "ok": True,
        "reason": "",
        "addressCount": 0,
    }
    assert "unit-test-provider-token" not in dumped
    assert "provider-api-key" not in dumped
    assert "unit-test-ssh-key" not in dumped
    assert "ssh-identity" not in dumped


def test_remote_provider_status_exposes_sanitized_ssh_supervisor_plan(
    test_client,
    monkeypatch,
    tmp_path,
):
    state_path = tmp_path / "routing-state.json"
    state_path.write_text(json.dumps(_route_state(transport="ssh")), encoding="utf-8")
    rps = _patch_state_path(monkeypatch, state_path)

    async def fake_fetch():
        return {
            "reachable": True,
            "valid": True,
            "ready": False,
            "status": "deferred",
            "reason": "ssh_transport_deferred",
            "secret": {"configured": True, "bytes": 24},
            "resolution": None,
        }

    async def fake_ssh_supervisor():
        return rps._safe_ssh_supervisor_status(
            {
                "schema": "ods.remote-provider-ssh-supervisor-plan.v1",
                "status": "planned",
                "ready": False,
                "readyToStart": True,
                "reason": "tunnel_process_not_started",
                "tunnelBaseUrl": "http://remote-provider-ssh-tunnel:18091/v1",
                "tunnels": [
                    {
                        "name": "inference",
                        "listenHost": "0.0.0.0",
                        "listenPort": 18091,
                        "targetHost": "127.0.0.1",
                        "targetPort": 8000,
                        "argv": ["ssh", "-F", "/dev/null", "ods@gpu.example.test"],
                        "value": "unit-test-key",
                    }
                ],
                "secrets": {
                    "sshIdentity": {
                        "configured": True,
                        "bytes": 14,
                        "path": "/state/remote-provider/secrets/ssh-identity",
                        "value": "unit-test-key",
                    },
                    "sshKnownHosts": {
                        "configured": True,
                        "bytes": 40,
                        "value": "AAAATEST",
                    },
                },
                "missingSecrets": [],
            }
        )

    monkeypatch.setattr(rps, "_fetch_egress_health", fake_fetch)
    monkeypatch.setattr(rps, "_fetch_ssh_supervisor_status", fake_ssh_supervisor)

    resp = test_client.get(
        "/api/remote-provider/status",
        headers=test_client.auth_headers,
    )

    body = resp.json()
    dumped = json.dumps(body, sort_keys=True)
    assert resp.status_code == 200
    assert body["sshSupervisor"]["valid"] is True
    assert body["sshSupervisor"]["status"] == "planned"
    assert body["sshSupervisor"]["readyToStart"] is True
    assert body["sshSupervisor"]["tunnelBaseUrl"] == "http://remote-provider-ssh-tunnel:18091/v1"
    assert body["sshSupervisor"]["tunnels"][0]["argv"][:3] == ["ssh", "-F", "/dev/null"]
    assert body["sshSupervisor"]["secrets"]["sshIdentity"] == {
        "configured": True,
        "bytes": 14,
    }
    assert "unit-test-key" not in dumped
    assert "AAAATEST" not in dumped
    assert "ssh-identity" not in dumped


def test_remote_provider_status_tolerates_unreachable_ssh_supervisor(
    test_client,
    monkeypatch,
    tmp_path,
):
    state_path = tmp_path / "routing-state.json"
    state_path.write_text(json.dumps(_route_state()), encoding="utf-8")
    rps = _patch_state_path(monkeypatch, state_path)

    async def fake_fetch():
        return {
            "reachable": True,
            "valid": True,
            "ready": True,
            "status": "ok",
            "reason": "ready",
            "secret": {"configured": True, "bytes": 24},
            "resolution": {"ok": True, "addressCount": 1},
        }

    async def fake_agent_request(*_args, **_kwargs):
        raise rps.AgentUnavailable("host agent route is down")

    monkeypatch.setattr(rps, "_fetch_egress_health", fake_fetch)
    monkeypatch.setattr(rps, "async_request_agent_json", fake_agent_request)

    resp = test_client.get(
        "/api/remote-provider/status",
        headers=test_client.auth_headers,
    )

    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "ready"
    assert body["sshSupervisor"]["reachable"] is False
    assert body["sshSupervisor"]["status"] == "unavailable"
    assert body["sshSupervisor"]["reason"] == "host_agent_unavailable"


def test_remote_provider_status_exposes_sanitized_probe_receipt(
    test_client,
    monkeypatch,
    tmp_path,
):
    status = {
        "proven": True,
        "reason": "provider-handshake-ok",
        "lastProbe": {
            "schema": "ods.remote-provider-probe-receipt.v1",
            "ok": True,
            "verifiedAt": "2026-07-26T00:00:00+00:00",
            "endpoint": "/v1/models",
            "httpStatus": 200,
            "contentType": "application/json",
            "modelCount": 1,
            "resolution": {"ok": True, "addressCount": 1, "raw": "93.184.216.34"},
            "value": "unit-test-provider-token",
        },
    }
    state_path = tmp_path / "routing-state.json"
    state_path.write_text(json.dumps(_route_state(status=status)), encoding="utf-8")
    rps = _patch_state_path(monkeypatch, state_path)

    async def fake_fetch():
        return {
            "reachable": True,
            "valid": True,
            "ready": True,
            "status": "ok",
            "reason": "ready",
            "secret": {"configured": True, "bytes": 24},
            "resolution": {"ok": True, "addressCount": 1},
        }

    monkeypatch.setattr(rps, "_fetch_egress_health", fake_fetch)

    resp = test_client.get(
        "/api/remote-provider/status",
        headers=test_client.auth_headers,
    )

    body = resp.json()
    dumped = json.dumps(body, sort_keys=True)
    assert resp.status_code == 200
    assert body["status"] == "ready"
    assert body["routeState"]["status"] == {
        "proven": True,
        "reason": "provider-handshake-ok",
        "lastProbe": {
            "schema": "ods.remote-provider-probe-receipt.v1",
            "ok": True,
            "verifiedAt": "2026-07-26T00:00:00+00:00",
            "endpoint": "/v1/models",
            "httpStatus": 200,
            "contentType": "application/json",
            "modelCount": 1,
            "resolution": {"ok": True, "addressCount": 1},
        },
    }
    assert "unit-test-provider-token" not in dumped
    assert "93.184.216.34" not in dumped


def test_remote_provider_status_invalid_state_is_diagnostic(
    test_client,
    monkeypatch,
    tmp_path,
):
    state_path = tmp_path / "routing-state.json"
    state_path.write_text("{not json", encoding="utf-8")
    rps = _patch_state_path(monkeypatch, state_path)

    async def fake_fetch():
        return {
            "reachable": True,
            "valid": True,
            "ready": False,
            "status": "disabled",
            "reason": "remote_route_disabled",
            "secret": {"configured": False, "bytes": 0},
            "resolution": None,
        }

    monkeypatch.setattr(rps, "_fetch_egress_health", fake_fetch)

    resp = test_client.get(
        "/api/remote-provider/status",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "invalid"
    assert body["routeState"]["valid"] is False
    assert body["routeState"]["errors"]


def test_remote_provider_status_reports_unreachable_egress(
    test_client,
    monkeypatch,
    tmp_path,
):
    state_path = tmp_path / "routing-state.json"
    state_path.write_text(json.dumps(_route_state()), encoding="utf-8")
    rps = _patch_state_path(monkeypatch, state_path)

    async def fake_fetch():
        return {
            "reachable": False,
            "valid": False,
            "ready": False,
            "status": "unreachable",
            "reason": "egress_unreachable",
            "secret": {"configured": False, "bytes": None},
            "resolution": None,
        }

    monkeypatch.setattr(rps, "_fetch_egress_health", fake_fetch)

    resp = test_client.get(
        "/api/remote-provider/status",
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["egress"]["reachable"] is False
    assert body["egress"]["reason"] == "egress_unreachable"


def test_remote_provider_plan_requires_auth(test_client):
    resp = test_client.post("/api/remote-provider/plan", json=_lifecycle_payload())
    assert resp.status_code == 401


def test_remote_provider_plan_proxies_to_host_agent(
    test_client,
    monkeypatch,
):
    from routers import remote_provider_status as rps

    calls = []

    async def fake_request(method, path, *, payload, timeout):
        calls.append((method, path, payload, timeout))
        return {
            "schema": "ods.remote-provider-lifecycle-operation.v1",
            "action": "test",
            "ok": True,
            "route": {
                "enabled": True,
                "provider": {
                    "baseUrl": "https://gpu.example.test/v1",
                    "model": "qwen/remote:latest",
                    "transport": "direct",
                },
            },
            "writes": {"routingState": False},
            "secretRefs": {
                "REMOTE_LLM_API_KEY": {"present": True, "value": "[REDACTED]"}
            },
        }

    monkeypatch.setattr(rps, "async_request_agent_json", fake_request)

    resp = test_client.post(
        "/api/remote-provider/plan",
        json=_lifecycle_payload(),
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    dumped = json.dumps(body, sort_keys=True)
    assert body["action"] == "test"
    assert body["secretRefs"]["REMOTE_LLM_API_KEY"]["value"] == "[REDACTED]"
    assert "unit-test-provider-token" not in dumped
    assert calls == [
        ("POST", "/v1/remote-provider/plan", _lifecycle_payload(), 10)
    ]


def test_remote_provider_plan_preserves_host_agent_validation_errors(
    test_client,
    monkeypatch,
):
    from routers import remote_provider_status as rps

    async def fake_request(*_args, **_kwargs):
        raise rps.AgentHTTPError(400, "remote provider base URL is required")

    monkeypatch.setattr(rps, "async_request_agent_json", fake_request)

    resp = test_client.post(
        "/api/remote-provider/plan",
        json={"action": "configure"},
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "remote provider base URL is required"


def test_remote_provider_probe_requires_auth(test_client):
    resp = test_client.post("/api/remote-provider/probe")
    assert resp.status_code == 401


def test_remote_provider_probe_posts_to_egress_and_sanitizes_receipt(
    test_client,
    monkeypatch,
):
    from routers import remote_provider_status as rps

    calls = []
    agent_calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "schema": "ods.remote-provider-egress-probe.v1",
                "ok": True,
                "transport": "ssh",
                "probe": {
                    "schema": "ods.remote-provider-probe-receipt.v1",
                    "ok": True,
                    "verifiedAt": "2026-07-26T00:00:00+00:00",
                    "endpoint": "/v1/models",
                    "httpStatus": 200,
                    "contentType": "application/json",
                    "modelCount": 1,
                    "resolution": {
                        "ok": True,
                        "addressCount": 0,
                        "raw": "127.0.0.1",
                    },
                    "value": "unit-test-provider-token",
                },
                "tunnel": {
                    "ok": True,
                    "ready": True,
                    "status": "running",
                    "reason": "ready",
                    "process": {
                        "status": "running",
                        "pid": 4242,
                        "argv": ["ssh", "-i", "/state/remote-provider/secrets/ssh-identity"],
                    },
                    "secretValue": "unit-test-ssh-key",
                },
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            calls.append(("timeout", kwargs.get("timeout")))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url):
            calls.append(("post", url))
            return FakeResponse()

    monkeypatch.setattr(rps, "EGRESS_URL", "http://egress.internal:8091/")
    monkeypatch.setattr(rps.httpx, "AsyncClient", FakeAsyncClient)
    async def fake_agent_request(method, path, *, payload, timeout):
        agent_calls.append((method, path, payload, timeout))
        return {
            "schema": "ods.remote-provider-proof-record.v1",
            "recorded": True,
            "status": {
                "proven": True,
                "reason": "provider-handshake-ok",
                "lastProbe": payload["probe"],
            },
        }

    monkeypatch.setattr(rps, "async_request_agent_json", fake_agent_request)

    resp = test_client.post(
        "/api/remote-provider/probe",
        headers=test_client.auth_headers,
    )

    body = resp.json()
    dumped = json.dumps(body, sort_keys=True)
    assert resp.status_code == 200
    assert body == {
        "schema": "ods.remote-provider-egress-probe.v1",
        "ok": True,
        "transport": "ssh",
        "probe": {
            "schema": "ods.remote-provider-probe-receipt.v1",
            "ok": True,
            "verifiedAt": "2026-07-26T00:00:00+00:00",
            "endpoint": "/v1/models",
            "httpStatus": 200,
            "contentType": "application/json",
            "modelCount": 1,
            "resolution": {"ok": True, "addressCount": 0},
        },
        "tunnel": {
            "ok": True,
            "ready": True,
            "status": "running",
            "reason": "ready",
            "process": {"status": "running", "pid": 4242},
        },
        "routeProof": {
            "recorded": True,
            "reachable": True,
            "schema": "ods.remote-provider-proof-record.v1",
            "status": {
                "proven": True,
                "reason": "provider-handshake-ok",
                "lastProbe": {
                    "schema": "ods.remote-provider-probe-receipt.v1",
                    "ok": True,
                    "verifiedAt": "2026-07-26T00:00:00+00:00",
                    "endpoint": "/v1/models",
                    "httpStatus": 200,
                    "contentType": "application/json",
                    "modelCount": 1,
                    "resolution": {"ok": True, "addressCount": 0},
                },
            },
        },
    }
    assert calls == [
        ("timeout", 60.0),
        ("post", "http://egress.internal:8091/probe"),
    ]
    assert agent_calls == [
        (
            "POST",
            "/v1/remote-provider/proof",
            {
                "schema": "ods.remote-provider-egress-probe.v1",
                "ok": True,
                "transport": "ssh",
                "probe": {
                    "schema": "ods.remote-provider-probe-receipt.v1",
                    "ok": True,
                    "verifiedAt": "2026-07-26T00:00:00+00:00",
                    "endpoint": "/v1/models",
                    "httpStatus": 200,
                    "contentType": "application/json",
                    "modelCount": 1,
                    "resolution": {"ok": True, "addressCount": 0},
                },
                "tunnel": {
                    "ok": True,
                    "ready": True,
                    "status": "running",
                    "reason": "ready",
                    "process": {"status": "running", "pid": 4242},
                },
            },
            1800.0,
        )
    ]
    assert "unit-test-provider-token" not in dumped
    assert "unit-test-ssh-key" not in dumped
    assert "ssh-identity" not in dumped
    assert "127.0.0.1" not in dumped


def test_remote_provider_probe_reports_nonfatal_proof_record_failure(
    test_client,
    monkeypatch,
):
    from routers import remote_provider_status as rps

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "schema": "ods.remote-provider-egress-probe.v1",
                "ok": True,
                "transport": "ssh",
                "probe": {
                    "schema": "ods.remote-provider-probe-receipt.v1",
                    "ok": True,
                    "verifiedAt": "2026-07-26T00:00:00+00:00",
                    "endpoint": "/v1/models",
                    "httpStatus": 200,
                    "modelCount": 1,
                    "resolution": {"ok": True, "addressCount": 0},
                },
                "tunnel": {"ok": True, "ready": True, "status": "running", "reason": "ready"},
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, _url):
            return FakeResponse()

    async def fake_agent_request(*_args, **_kwargs):
        raise rps.AgentHTTPError(409, "unit-test-provider-token ssh-identity")

    monkeypatch.setattr(rps.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(rps, "async_request_agent_json", fake_agent_request)

    resp = test_client.post(
        "/api/remote-provider/probe",
        headers=test_client.auth_headers,
    )

    body = resp.json()
    dumped = json.dumps(body, sort_keys=True)
    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["routeProof"] == {
        "recorded": False,
        "reachable": True,
        "reason": "host_agent_http_409",
        "statusCode": 409,
    }
    assert "unit-test-provider-token" not in dumped
    assert "ssh-identity" not in dumped


def test_remote_provider_probe_preserves_sanitized_egress_errors(
    test_client,
    monkeypatch,
):
    from routers import remote_provider_status as rps

    class FakeResponse:
        status_code = 503

        def json(self):
            return {
                "error": {
                    "type": "ssh_tunnel_not_ready",
                    "message": (
                        "SSH tunnel is not ready: unit-test-provider-token "
                        "/state/remote-provider/secrets/ssh-identity"
                    ),
                    "code": "503",
                }
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, _url):
            return FakeResponse()

    monkeypatch.setattr(rps.httpx, "AsyncClient", FakeAsyncClient)

    resp = test_client.post(
        "/api/remote-provider/probe",
        headers=test_client.auth_headers,
    )

    body = resp.json()
    dumped = json.dumps(body, sort_keys=True)
    assert resp.status_code == 503
    assert body["detail"] == {
        "type": "ssh_tunnel_not_ready",
        "message": "SSH tunnel is not ready",
        "code": "503",
    }
    assert "unit-test-provider-token" not in dumped
    assert "ssh-identity" not in dumped


def test_remote_provider_apply_requires_auth(test_client):
    resp = test_client.post("/api/remote-provider/apply", json=_lifecycle_payload())
    assert resp.status_code == 401


def test_remote_provider_apply_proxies_to_host_agent(
    test_client,
    monkeypatch,
):
    from routers import remote_provider_status as rps

    calls = []

    async def fake_request(method, path, *, payload, timeout):
        calls.append((method, path, payload, timeout))
        return {
            "schema": "ods.remote-provider-lifecycle-operation.v1",
            "action": "configure",
            "ok": True,
            "applied": True,
            "mutated": True,
            "rollback": {"attempted": False, "ok": None},
            "secretRefs": {
                "REMOTE_LLM_API_KEY": {"present": True, "value": "[REDACTED]"}
            },
        }

    monkeypatch.setattr(rps, "async_request_agent_json", fake_request)
    payload = _lifecycle_payload()
    payload["action"] = "configure"

    resp = test_client.post(
        "/api/remote-provider/apply",
        json=payload,
        headers=test_client.auth_headers,
    )

    body = resp.json()
    dumped = json.dumps(body, sort_keys=True)
    assert resp.status_code == 200
    assert body["action"] == "configure"
    assert body["mutated"] is True
    assert "unit-test-provider-token" not in dumped
    assert calls == [(
        "POST",
        "/v1/remote-provider/apply",
        payload,
        rps.LIFECYCLE_APPLY_TIMEOUT_SECONDS,
    )]


def test_remote_provider_apply_preserves_host_agent_validation_errors(
    test_client,
    monkeypatch,
):
    from routers import remote_provider_status as rps

    async def fake_request(*_args, **_kwargs):
        raise rps.AgentHTTPError(500, "Remote provider apply failed: disk full")

    monkeypatch.setattr(rps, "async_request_agent_json", fake_request)

    resp = test_client.post(
        "/api/remote-provider/apply",
        json=_lifecycle_payload(),
        headers=test_client.auth_headers,
    )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Remote provider apply failed: disk full"


def test_remote_provider_enable_completes_staged_ssh_proof(
    test_client,
    monkeypatch,
):
    from routers import remote_provider_status as rps

    calls = []

    async def fake_request(method, path, *, payload, timeout):
        calls.append((method, path, payload, timeout))
        return {
            "schema": "ods.remote-provider-lifecycle-operation.v1",
            "action": "enable",
            "ok": True,
            "applied": False,
            "staged": True,
            "mutated": True,
            "rollback": {"attempted": False, "ok": None},
        }

    async def fake_probe():
        return {
            "ok": True,
            "probe": {"ok": True, "httpStatus": 200},
            "routeProof": {
                "recorded": True,
                "activation": {"active": True, "proven": True},
            },
        }

    monkeypatch.setattr(rps, "async_request_agent_json", fake_request)
    monkeypatch.setattr(rps, "remote_provider_probe", fake_probe)

    resp = test_client.post(
        "/api/remote-provider/enable",
        headers=test_client.auth_headers,
    )

    body = resp.json()
    assert resp.status_code == 200
    assert body["action"] == "enable"
    assert body["applied"] is True
    assert body["staged"] is False
    assert body["routeProof"]["recorded"] is True
    assert calls == [(
        "POST",
        "/v1/remote-provider/apply",
        {"action": "enable"},
        rps.LIFECYCLE_APPLY_TIMEOUT_SECONDS,
    )]


def test_remote_provider_enable_repauses_after_ssh_probe_failure(
    test_client,
    monkeypatch,
):
    from routers import remote_provider_status as rps

    actions = []

    async def fake_request(_method, _path, *, payload, timeout):
        assert timeout == rps.LIFECYCLE_APPLY_TIMEOUT_SECONDS
        actions.append(payload["action"])
        if payload["action"] == "enable":
            return {
                "action": "enable",
                "applied": False,
                "staged": True,
                "mutated": True,
            }
        return {"action": "disable", "applied": True, "mutated": True}

    async def failing_probe():
        raise rps.HTTPException(status_code=503, detail={"type": "ssh_tunnel_not_ready"})

    monkeypatch.setattr(rps, "async_request_agent_json", fake_request)
    monkeypatch.setattr(rps, "remote_provider_probe", failing_probe)

    resp = test_client.post(
        "/api/remote-provider/enable",
        headers=test_client.auth_headers,
    )

    body = resp.json()
    assert resp.status_code == 503
    assert body["detail"]["error"] == "remote_provider_enable_failed"
    assert body["detail"]["rollback"] == {"attempted": True, "ok": True}
    assert actions == ["enable", "disable"]


def test_remote_provider_enable_reports_unexpected_rollback_failure(
    test_client,
    monkeypatch,
):
    from routers import remote_provider_status as rps

    actions = []

    async def fake_request(_method, _path, *, payload, timeout):
        assert timeout == rps.LIFECYCLE_APPLY_TIMEOUT_SECONDS
        actions.append(payload["action"])
        if payload["action"] == "enable":
            return {
                "action": "enable",
                "applied": False,
                "staged": True,
                "mutated": True,
            }
        raise RuntimeError("simulated rollback transport failure")

    async def failing_probe():
        raise rps.HTTPException(status_code=503, detail={"type": "ssh_tunnel_not_ready"})

    monkeypatch.setattr(rps, "async_request_agent_json", fake_request)
    monkeypatch.setattr(rps, "remote_provider_probe", failing_probe)

    resp = test_client.post(
        "/api/remote-provider/enable",
        headers=test_client.auth_headers,
    )

    body = resp.json()
    assert resp.status_code == 503
    assert body["detail"]["error"] == "remote_provider_enable_failed"
    assert body["detail"]["rollback"] == {"attempted": True, "ok": False}
    assert actions == ["enable", "disable"]
