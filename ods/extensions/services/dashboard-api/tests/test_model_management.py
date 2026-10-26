"""Dashboard capability and runtime controls, with no live host-agent access."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from unittest.mock import AsyncMock

import pytest


MANAGED = {"managed": True, "canActivate": True, "canUnload": True, "running": True}
STOPPED = {**MANAGED, "canActivate": False, "running": False}
UNMANAGED = {key: False for key in MANAGED}
UNVERIFIED = {**UNMANAGED, "managed": None, "reason": "Runtime management could not be verified"}


@pytest.fixture
def model_runtime(monkeypatch, tmp_path):
    import helpers
    import routers.models as router

    install = tmp_path / "ods"
    data = install / "data"
    data.mkdir(parents=True)
    # A migrated WSL Portal: llama-server.exe on Windows, behind the bridge.
    (install / ".env").write_text("ODS_MODE=local\n", encoding="utf-8")
    monkeypatch.setattr(router, "INSTALL_DIR", str(install))
    monkeypatch.setattr(router, "DATA_DIR", str(data))
    monkeypatch.setattr(router, "_ENV_PATH", install / ".env")
    monkeypatch.setattr(router, "_LIBRARY_PATH", install / "config" / "model-library.json")
    monkeypatch.setattr(router, "_MODELS_DIR", data / "models")
    monkeypatch.setattr(helpers, "_PERF_FILE", data / "performance.json")
    monkeypatch.setattr(router, "ODS_MODE_EFFECTIVE", "local")
    monkeypatch.setattr(router, "LLM_BACKEND", "llama-server")
    monkeypatch.setattr(router, "read_live_env_values", lambda _keys: {
        "LLM_BACKEND": "llama-server", "ODS_HOST_LLM_TRANSPORT": "model-router",
        "AMD_INFERENCE_RUNTIME_MODE": "windows-portal-llama-server",
    })
    monkeypatch.setattr(router, "get_gpu_info", lambda: None)
    monkeypatch.setattr(router, "get_loaded_model", AsyncMock(return_value=None))
    monkeypatch.setattr(router, "_fetch_llama_loaded_model", AsyncMock(return_value=None))
    monkeypatch.setattr(router, "get_llama_metrics", AsyncMock(return_value={}))
    monkeypatch.setattr(router, "get_llama_context_size", AsyncMock(return_value=None))
    monkeypatch.setattr(router, "_verified_activation_context", lambda _model: None)
    monkeypatch.setattr(router, "_get_agent_model_status", lambda: {"status": "idle"})
    monkeypatch.setattr(router, "_installed_model_paths", lambda: {})
    monkeypatch.setattr(router, "_load_library", lambda: [])
    monkeypatch.setattr(router, "pixel_stream_active", lambda: False)

    def unexpected_request(*args, **kwargs):
        raise AssertionError(f"Unexpected host-agent request: {args!r} {kwargs!r}")

    monkeypatch.setattr(router, "request_agent_json", unexpected_request)
    return router


@pytest.mark.parametrize("capability", [
    MANAGED,
    {**MANAGED, "running": False, "canActivate": False},
    UNMANAGED,
])
def test_catalog_exposes_verified_management(test_client, model_runtime, monkeypatch, capability):
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return capability

    monkeypatch.setattr(model_runtime, "request_agent_json", request)
    response = test_client.get("/api/models", headers=test_client.auth_headers)

    assert response.status_code == 200
    assert response.json()["hostRuntime"] is True
    assert "externalLemonade" not in response.json()
    assert response.json()["modelManagement"] == capability
    assert calls == [("GET", "/v1/model/management", {"timeout": 20})]


@pytest.mark.parametrize("invalid", [
    None, [], {}, {**MANAGED, "managed": "true"}, {**MANAGED, "running": 1},
    {**MANAGED, "managed": False}, {**MANAGED, "running": False},
])
def test_management_fails_closed_on_malformed_evidence(model_runtime, monkeypatch, invalid):
    monkeypatch.setattr(model_runtime, "request_agent_json", lambda *_args, **_kwargs: invalid)
    capability = model_runtime._model_management()
    assert capability == UNVERIFIED


def test_management_carries_the_launchers_error_and_start_refusal(model_runtime, monkeypatch):
    stopped = {**MANAGED, "running": False, "canActivate": False}
    refused = {**stopped, "runtimeError": "The model was not started: Windows can lend it only 1.0 GiB right now.",
               "startRefusal": "memory_refused"}
    monkeypatch.setattr(model_runtime, "request_agent_json", lambda *_args, **_kwargs: refused)
    assert model_runtime._model_management() == refused
    long_text = {**stopped, "runtimeError": "x" * 5000, "startRefusal": "out_of_memory"}
    monkeypatch.setattr(model_runtime, "request_agent_json", lambda *_args, **_kwargs: long_text)
    assert model_runtime._model_management() == {**stopped, "runtimeError": "x" * 1024}
    for value in ({**MANAGED, "runtimeError": "running models carry no error"},
                  {**UNMANAGED, "runtimeError": "not ours", "startRefusal": "memory_refused"},
                  {**stopped, "runtimeError": 7, "startRefusal": "memory_refused"},
                  {**stopped, "runtimeError": "  "}):
        monkeypatch.setattr(model_runtime, "request_agent_json", lambda *_args, value=value, **_kwargs: value)
        capability = model_runtime._model_management()
        assert "runtimeError" not in capability and "startRefusal" not in capability


def test_management_fails_closed_when_agent_unavailable(model_runtime, monkeypatch):
    def request(*_args, **_kwargs):
        raise model_runtime.AgentUnavailable("fixture unavailable")

    monkeypatch.setattr(model_runtime, "request_agent_json", request)
    assert model_runtime._model_management() == UNVERIFIED


def test_transient_management_failure_is_unknown_and_recovers_without_granting_control(
    test_client, model_runtime, monkeypatch,
):
    healthy = False

    def request(method, path, **kwargs):
        assert method == "GET" and path == "/v1/model/management"
        if not healthy:
            raise model_runtime.AgentHTTPError(503, "fixture interop timeout")
        return MANAGED

    def no_lookup(_model_id):
        raise AssertionError("Unverified activation reached catalog lookup")

    monkeypatch.setattr(model_runtime, "request_agent_json", request)
    monkeypatch.setattr(model_runtime, "_find_loadable_model", no_lookup)
    unavailable = test_client.get("/api/models", headers=test_client.auth_headers)
    assert unavailable.status_code == 200
    assert unavailable.json()["modelManagement"] == UNVERIFIED
    blocked = test_client.post("/api/models/fixture/load", headers=test_client.auth_headers)
    assert blocked.status_code == 409

    healthy = True
    recovered = test_client.get("/api/models", headers=test_client.auth_headers)
    assert recovered.status_code == 200
    assert recovered.json()["modelManagement"] == MANAGED


def test_management_preserves_safe_reason_and_omits_agent_internals(model_runtime, monkeypatch):
    monkeypatch.setattr(model_runtime, "request_agent_json", lambda *_args, **_kwargs: {
        **UNMANAGED, "reason": "The owned task could not be verified", "planPath": "private-path",
    })
    assert model_runtime._model_management() == {
        **UNMANAGED, "reason": "The owned task could not be verified",
    }


def test_nonexternal_catalog_never_requests_management(test_client, model_runtime, monkeypatch):
    monkeypatch.setattr(model_runtime, "_windows_hosted_runtime", lambda: False)
    response = test_client.get("/api/models", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json()["hostRuntime"] is False
    assert response.json().get("modelManagement") is None
    assert model_runtime._model_management() == UNMANAGED


@pytest.mark.parametrize("capability", [
    UNMANAGED,
    {**MANAGED, "managed": False},
    {**MANAGED, "canActivate": False, "running": False},
])
def test_external_activation_requires_owned_and_available_capability(
    test_client, model_runtime, monkeypatch, capability,
):
    monkeypatch.setattr(model_runtime, "request_agent_json", lambda *_args, **_kwargs: capability)

    def no_lookup(_model_id):
        raise AssertionError("Unowned or unavailable activation reached catalog lookup")

    monkeypatch.setattr(model_runtime, "_find_loadable_model", no_lookup)
    response = test_client.post("/api/models/fixture/load", headers=test_client.auth_headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "external_runtime_unmanaged"


def test_owned_external_activation_preserves_existing_context_transaction(
    test_client, model_runtime, monkeypatch,
):
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET" and path == "/v1/model/management":
            return MANAGED
        assert method == "POST" and path == "/v1/model/activate"
        return {"status": "activated", "context_length": kwargs["payload"]["context_length"]}

    monkeypatch.setattr(model_runtime, "request_agent_json", request)
    monkeypatch.setattr(model_runtime, "_find_loadable_model", lambda _id: {"id": "fixture", "gguf_file": "fixture.gguf"})
    monkeypatch.setattr(model_runtime, "_already_active_model", lambda *_args: (False, None))
    monkeypatch.setattr(model_runtime, "_bootstrap_upgrade_download_conflict", lambda: None)
    invalidated = []
    monkeypatch.setattr(model_runtime, "_invalidate_agent_model_status_cache", lambda: invalidated.append(True))
    response = test_client.post(
        "/api/models/fixture/load", headers=test_client.auth_headers, json={"context_length": 8192},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "activated", "context_length": 8192}
    assert calls == [
        ("GET", "/v1/model/management", {"timeout": 20}),
        ("POST", "/v1/model/activate", {"payload": {"model_id": "fixture", "context_length": 8192}, "timeout": 2700}),
    ]
    assert invalidated == [True]


@pytest.mark.parametrize("operation", ["stop", "start"])
def test_runtime_controls_require_auth(test_client, model_runtime, operation):
    response = test_client.post(f"/api/models/runtime/{operation}", json={})
    assert response.status_code == 401


@pytest.mark.parametrize(("operation", "status"), [("stop", "stopped"), ("start", "started")])
def test_runtime_controls_confirm_ack_and_invalidate_status(
    test_client, model_runtime, monkeypatch, operation, status,
):
    calls = []
    invalidated = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"status": status, "privatePath": "must not be projected"}

    monkeypatch.setattr(model_runtime, "request_agent_json", request)
    monkeypatch.setattr(model_runtime, "_invalidate_agent_model_status_cache", lambda: invalidated.append(True))
    response = test_client.post(f"/api/models/runtime/{operation}", headers=test_client.auth_headers, json={})
    assert response.status_code == 200
    assert response.json() == {"status": status}
    assert response.headers["cache-control"] == "no-store"
    assert calls == [("POST", f"/v1/model/runtime/{operation}", {"payload": {}, "timeout": 1200})]
    assert invalidated == [True]


@pytest.mark.parametrize(("operation", "body"), [("restart", {}), ("stop", {"force": True}), ("start", {"model_id": "other"})])
def test_runtime_controls_reject_unsupported_commands(test_client, model_runtime, operation, body):
    response = test_client.post(f"/api/models/runtime/{operation}", headers=test_client.auth_headers, json=body)
    assert response.status_code == 400


@pytest.mark.parametrize("operation", ["stop", "start"])
def test_runtime_controls_do_not_interrupt_portal(test_client, model_runtime, monkeypatch, operation):
    monkeypatch.setattr(model_runtime, "pixel_stream_active", lambda: True)
    response = test_client.post(f"/api/models/runtime/{operation}", headers=test_client.auth_headers, json={})
    assert response.status_code == 409
    assert "active Portal response" in response.json()["detail"]


@pytest.mark.parametrize("reply", [None, {}, {"status": "started"}, {"status": "stopping"}])
def test_runtime_control_requires_matching_terminal_ack(test_client, model_runtime, monkeypatch, reply):
    monkeypatch.setattr(model_runtime, "request_agent_json", lambda *_args, **_kwargs: reply)
    invalidated = []
    monkeypatch.setattr(model_runtime, "_invalidate_agent_model_status_cache", lambda: invalidated.append(True))
    response = test_client.post("/api/models/runtime/stop", headers=test_client.auth_headers, json={})
    assert response.status_code == 502
    assert "not confirmed" in response.json()["detail"]
    assert invalidated == [True]


@pytest.mark.parametrize(("error", "status", "detail"), [
    ("busy", 409, {"code": "model_lifecycle_busy", "error": "Activation in progress"}),
    ("unavailable", 503, "Host agent unreachable: fixture unavailable"),
    ("protocol", 502, "Invalid host agent response: fixture malformed"),
])
def test_runtime_control_preserves_failure_and_invalidates_status(
    test_client, model_runtime, monkeypatch, error, status, detail,
):
    def request(*_args, **_kwargs):
        if error == "busy":
            raise model_runtime.AgentHTTPError(409, "conflict", json.dumps(detail))
        if error == "unavailable":
            raise model_runtime.AgentUnavailable("fixture unavailable")
        raise model_runtime.AgentProtocolError("fixture malformed")

    invalidated = []
    monkeypatch.setattr(model_runtime, "request_agent_json", request)
    monkeypatch.setattr(model_runtime, "_invalidate_agent_model_status_cache", lambda: invalidated.append(True))
    response = test_client.post("/api/models/runtime/start", headers=test_client.auth_headers, json={})
    assert response.status_code == status
    assert response.json()["detail"] == detail
    assert invalidated == [True]


def _recording(calls, answers):
    """A host-agent stub that records each path and answers with the latest of ``answers``."""
    def request(_method, path, **_kwargs):
        calls.append(path)
        return answers[-1]
    return request


def _catalog_management(test_client):
    response = test_client.get("/api/models", headers=test_client.auth_headers)
    assert response.status_code == 200
    return response.json()["modelManagement"]


def test_catalog_reuses_a_proof_the_live_runtime_agrees_with(test_client, model_runtime, monkeypatch):
    calls = []
    monkeypatch.setattr(model_runtime, "request_agent_json", _recording(calls, [MANAGED]))
    monkeypatch.setattr(model_runtime, "get_loaded_model", AsyncMock(return_value="fixture"))
    assert [_catalog_management(test_client) for _ in range(3)] == [MANAGED] * 3
    assert calls == ["/v1/model/management"]


def test_catalog_proves_again_when_the_live_runtime_disagrees(test_client, model_runtime, monkeypatch):
    calls = []
    answers = [MANAGED]
    monkeypatch.setattr(model_runtime, "request_agent_json", _recording(calls, answers))
    loaded = AsyncMock(return_value="fixture")
    monkeypatch.setattr(model_runtime, "get_loaded_model", loaded)
    assert _catalog_management(test_client) == MANAGED
    # The model stopped answering: a crash, or a stop made outside this dashboard.
    loaded.return_value = None
    answers.append(STOPPED)
    assert _catalog_management(test_client) == STOPPED
    assert _catalog_management(test_client) == STOPPED
    assert calls == ["/v1/model/management"] * 2


@pytest.mark.parametrize("status", [
    {"status": "idle", "lifecycleActive": True, "activeOperation": "model_runtime"},
    {"status": "idle", "modelTransactionPending": True},
])
def test_owned_lifecycle_operations_always_get_a_fresh_proof(test_client, model_runtime, monkeypatch, status):
    calls = []
    monkeypatch.setattr(model_runtime, "request_agent_json", _recording(calls, [MANAGED]))
    monkeypatch.setattr(model_runtime, "get_loaded_model", AsyncMock(return_value="fixture"))
    _catalog_management(test_client)
    monkeypatch.setattr(model_runtime, "_get_agent_model_status", lambda: status)
    _catalog_management(test_client)
    _catalog_management(test_client)
    assert calls == ["/v1/model/management"] * 3


def test_runtime_and_recovery_requests_forget_the_proof(test_client, model_runtime, monkeypatch):
    calls = []

    def request(method, path, **_kwargs):
        calls.append(path)
        if path == "/v1/model/recover":
            raise model_runtime.AgentUnavailable("fixture unavailable")
        return {"status": "stopped"} if method == "POST" else MANAGED

    monkeypatch.setattr(model_runtime, "request_agent_json", request)
    monkeypatch.setattr(model_runtime, "get_loaded_model", AsyncMock(return_value="fixture"))
    _catalog_management(test_client)
    assert test_client.post("/api/models/runtime/stop", headers=test_client.auth_headers, json={}).status_code == 200
    _catalog_management(test_client)
    assert test_client.post("/api/models/recovery", headers=test_client.auth_headers, json={}).status_code == 503
    _catalog_management(test_client)
    assert calls == ["/v1/model/management", "/v1/model/runtime/stop", "/v1/model/management",
                     "/v1/model/recover", "/v1/model/management"]


def test_catalog_proves_ownership_alongside_the_other_reads(test_client, model_runtime, monkeypatch):
    proving = threading.Event()
    seen = []

    def request(_method, _path, **_kwargs):
        proving.set()
        return MANAGED

    async def discover():
        # Taken one after the other, the proof would only start after this returned.
        for _ in range(500):
            if proving.is_set():
                break
            await asyncio.sleep(0.01)
        seen.append(proving.is_set())
        return "fixture"

    monkeypatch.setattr(model_runtime, "request_agent_json", request)
    monkeypatch.setattr(model_runtime, "get_loaded_model", discover)
    assert _catalog_management(test_client) == MANAGED
    assert seen == [True]


def test_older_proof_is_shown_while_a_fresh_one_is_taken_in_the_background(model_runtime, monkeypatch):
    fresh = {**MANAGED, "reason": "fresh"}
    calls = []
    monkeypatch.setattr(model_runtime, "request_agent_json", _recording(calls, [fresh]))
    monkeypatch.setattr(model_runtime, "_model_management_remembered", (time.monotonic() - 20, MANAGED))

    async def reads():
        shown = await model_runtime._catalog_model_management(None, {"status": "idle"}, "fixture")
        await model_runtime._model_management_proof_future
        return shown, await model_runtime._catalog_model_management(None, {"status": "idle"}, "fixture")

    assert asyncio.run(reads()) == (MANAGED, fresh)
    assert calls == ["/v1/model/management"]


def test_catalog_never_reuses_a_proof_past_its_age_limit(model_runtime, monkeypatch):
    calls = []
    monkeypatch.setattr(model_runtime, "request_agent_json", _recording(calls, [STOPPED]))
    expired = time.monotonic() - model_runtime._MODEL_MANAGEMENT_MAX_AGE_SECONDS - 1
    monkeypatch.setattr(model_runtime, "_model_management_remembered", (expired, STOPPED))
    assert asyncio.run(model_runtime._catalog_model_management(None, {"status": "idle"}, None)) == STOPPED
    assert calls == ["/v1/model/management"]


def test_concurrent_catalog_reads_share_one_proof(model_runtime, monkeypatch):
    calls = []

    def request(_method, path, **_kwargs):
        calls.append(path)
        time.sleep(0.05)
        return MANAGED

    monkeypatch.setattr(model_runtime, "request_agent_json", request)

    async def reads():
        return await asyncio.gather(*(
            model_runtime._catalog_model_management(None, {"status": "idle"}, "fixture") for _ in range(3)))

    assert asyncio.run(reads()) == [MANAGED] * 3
    assert calls == ["/v1/model/management"]


def test_a_proof_overtaken_by_a_runtime_change_is_taken_again(model_runtime, monkeypatch):
    calls = []

    def request(_method, path, **_kwargs):
        calls.append(path)
        if len(calls) == 1:
            # The runtime changes while the first proof is still running.
            model_runtime._forget_model_management()
            return MANAGED
        return STOPPED

    monkeypatch.setattr(model_runtime, "request_agent_json", request)
    assert asyncio.run(model_runtime._catalog_model_management(None, {"status": "idle"}, None)) == STOPPED
    assert calls == ["/v1/model/management"] * 2
    assert model_runtime._model_management_remembered[1] == STOPPED
