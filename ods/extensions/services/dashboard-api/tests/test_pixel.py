"""Security and streaming contracts for the Pixel dashboard bridge."""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError


os.environ.setdefault("DASHBOARD_API_KEY", "dashboard-test-key")
os.environ.setdefault("ODS_INSTALL_DIR", "/tmp/ods-test-install")
os.environ.setdefault("ODS_DATA_DIR", "/tmp/ods-test-data")
os.environ.setdefault("ODS_EXTENSIONS_DIR", "/tmp/ods-test-extensions")
os.environ.setdefault("GPU_BACKEND", "nvidia")
os.environ.setdefault("ODS_MODE", "local")

DASHBOARD_API_DIR = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, DASHBOARD_API_DIR)

from routers import pixel  # noqa: E402
import pixel_runtime_state  # noqa: E402
import pixel_chat_identity  # noqa: E402
from pixel_runtime_state import pixel_stream_active  # noqa: E402


EDGE_KEY = "e" * 64


class FakeResponse:
    def __init__(self, status=200, content_type="application/json", chunks=()):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self._chunks = list(chunks)

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class FakeStreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args):
        return False


class FakeClient:
    def __init__(self, response, capture=None):
        self.response = response
        self.capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def aclose(self):
        return None

    def stream(self, method, url, **kwargs):
        if self.capture is not None:
            self.capture.update({"method": method, "url": url, **kwargs})
        return FakeStreamContext(self.response)


class CancelAwareClient(FakeClient):
    def __init__(self, response, calls):
        super().__init__(response)
        self.calls = calls

    def stream(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if url.endswith("/v1/chat/cancel"):
            return FakeStreamContext(
                FakeResponse(
                    content_type="application/json",
                    chunks=[b'{"aborted":true}'],
                )
            )
        return FakeStreamContext(self.response)


class ConnectedRequest:
    async def is_disconnected(self):
        return False


class DisconnectedRequest:
    async def is_disconnected(self):
        return True


class SilentResponse(FakeResponse):
    def __init__(self):
        super().__init__(content_type="text/event-stream")
        self.cancelled = asyncio.Event()

    async def aiter_bytes(self):
        try:
            await asyncio.Future()
        finally:
            self.cancelled.set()
        yield b""  # pragma: no cover - keeps this an async generator


async def stream_body(response):
    return b"".join([chunk async for chunk in response.body_iterator])


@pytest.fixture(autouse=True)
def pixel_env(monkeypatch):
    async def saved_identity(*_args, **_kwargs):
        return {"schemaVersion": 1, "revision": 1, "displayName": "Portal"}
    monkeypatch.setattr(pixel_chat_identity, "async_request_json", saved_identity)
    monkeypatch.setenv("PIXEL_OPENWEBUI_KEY", EDGE_KEY)
    monkeypatch.setenv("PIXEL_EDGE_URL", "http://pixel-edge:9595")

    async def idle_model_lifecycle(*_args, **_kwargs):
        return {"status": "idle", "lifecycleActive": False, "activeOperation": None}

    monkeypatch.setattr(pixel, "request_agent_json", idle_model_lifecycle)


@pytest.mark.parametrize(
    "value",
    [
        "https://pixel-edge:9595",
        "http://other:9595",
        "http://pixel-edge:8080",
        "http://user:pass@pixel-edge:9595",
        "http://pixel-edge:9595/path",
        "http://pixel-edge:9595?query=1",
        "http://pixel-edge:9595#fragment",
        "http://pixel-edge:notaport",
    ],
)
def test_edge_url_rejects_every_origin_escape(value):
    with pytest.raises(ValueError):
        pixel._validate_edge_url(value)


def test_pixel_config_is_optional_but_strict(monkeypatch):
    monkeypatch.delenv("PIXEL_OPENWEBUI_KEY", raising=False)
    assert pixel._pixel_config() is None
    for value in ("short", f" {EDGE_KEY}", f"{EDGE_KEY}\n", "x" * 4097):
        monkeypatch.setenv("PIXEL_OPENWEBUI_KEY", value)
        with pytest.raises(RuntimeError):
            pixel._pixel_config()


def test_request_schema_forbids_extra_fields_and_unsafe_ids():
    with pytest.raises(ValidationError):
        pixel.ChatStreamRequest.model_validate(
            {"chat_id": "safe", "messages": [{"role": "user", "content": "ok", "extra": 1}]}
        )
    with pytest.raises(ValidationError):
        pixel.ChatStreamRequest.model_validate(
            {"chat_id": "../../escape", "messages": [{"role": "user", "content": "ok"}]}
        )
    with pytest.raises(ValidationError):
        pixel.ChatStreamRequest.model_validate(
            {"chat_id": "safe", "messages": [{"role": "tool", "content": "ok"}]}
        )
    with pytest.raises(ValidationError):
        pixel.ChatCancelRequest.model_validate({"chat_id": "../../escape"})
    with pytest.raises(ValidationError):
        pixel.ChatCancelRequest.model_validate({"chat_id": "safe", "extra": True})


def test_request_aggregate_is_bounded_in_utf8_bytes():
    with pytest.raises(ValidationError):
        pixel.ChatStreamRequest.model_validate(
            {
                "chat_id": "safe",
                "messages": [{"role": "user", "content": "😀" * (16 * 1024)} for _ in range(5)],
            }
        )


def test_stream_timeout_budget_outlives_pixel_edge():
    assert pixel._CHAT_STREAM_TIMEOUT_SECONDS == 2040.0


def test_routes_require_dashboard_auth():
    app = FastAPI()
    app.include_router(pixel.router)
    client = TestClient(app)
    assert client.get("/api/pixel/status").status_code in {401, 403}
    assert client.post("/api/pixel/chat/stream", json={}).status_code in {401, 403}
    assert client.post("/api/pixel/chat/cancel", json={"chat_id": "safe"}).status_code in {401, 403}
    assert client.post("/api/pixel/chat/activity", json={"chat_id": "safe"}).status_code in {401, 403}
    assert client.get(
        "/api/pixel/ops/ops-1788127319657-f3262c99a419",
        params={"plan_hash": "e" * 64},
    ).status_code in {401, 403}


@pytest.mark.asyncio
async def test_operations_status_returns_only_exact_host_projection(monkeypatch):
    job_id = "ops-1788127319657-f3262c99a419"
    plan_hash = "e" * 64
    secret = "protected-plan-argv-must-not-appear"

    async def exact_projection(method, path, **kwargs):
        assert method == "GET"
        assert path == "/v1/pixel/ops-status"
        assert kwargs["params"] == {"job_id": job_id, "plan_hash": plan_hash}
        return {
            "schemaVersion": 1,
            "kind": "ods-pixel-operations-status",
            "jobId": job_id,
            "planHash": plan_hash,
            "status": "awaiting-approval",
            "riskTier": "managed",
            "approvalRequired": True,
            "updatedAt": "2026-08-30T22:01:59Z",
            "approvalCommand": f"/opt/ods/bin/ods-pixel-approve {job_id} {plan_hash} --confirm",
        }

    monkeypatch.setattr(pixel, "request_agent_json", exact_projection)
    result = await pixel.pixel_operations_status(job_id, plan_hash)
    assert result["jobId"] == job_id
    assert result["planHash"] == plan_hash
    assert result["status"] == "awaiting-approval"
    assert secret not in json.dumps(result)


@pytest.mark.asyncio
async def test_operations_status_rejects_unbound_host_projection(monkeypatch):
    job_id = "ops-1788127319657-f3262c99a419"
    plan_hash = "e" * 64

    async def mismatched_projection(*_args, **_kwargs):
        return {
            "schemaVersion": 1,
            "kind": "ods-pixel-operations-status",
            "jobId": job_id,
            "planHash": "f" * 64,
            "status": "awaiting-approval",
            "riskTier": "managed",
            "approvalRequired": True,
            "updatedAt": "2026-08-30T22:01:59Z",
            "approvalCommand": "unsafe",
        }

    monkeypatch.setattr(pixel, "request_agent_json", mismatched_projection)
    with pytest.raises(HTTPException) as exc:
        await pixel.pixel_operations_status(job_id, plan_hash)
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_explicit_cancel_forwards_only_validated_chat_id_and_edge_key():
    calls = []
    body = pixel.ChatCancelRequest.model_validate({"chat_id": "conversation_1"})
    with patch.object(
        pixel.httpx,
        "AsyncClient",
        return_value=CancelAwareClient(FakeResponse(), calls),
    ):
        result = await pixel.pixel_chat_cancel(body)

    assert result == {"aborted": True}
    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "http://pixel-edge:9595/v1/chat/cancel"
    assert calls[0]["json"] == {"user": "conversation_1"}
    assert calls[0]["headers"]["Authorization"] == f"Bearer {EDGE_KEY}"
    assert "dashboard-test-key" not in json.dumps(calls)


@pytest.mark.asyncio
async def test_explicit_cancel_reports_unconfirmed_without_inventing_success():
    body = pixel.ChatCancelRequest.model_validate({"chat_id": "conversation_1"})
    upstream = FakeResponse(content_type="application/json", chunks=[b'{"aborted":false}'])
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(upstream)):
        assert await pixel.pixel_chat_cancel(body) == {"aborted": False}


@pytest.mark.asyncio
async def test_status_is_disabled_without_a_key(monkeypatch):
    monkeypatch.delenv("PIXEL_OPENWEBUI_KEY", raising=False)
    assert await pixel.pixel_status() == {
        "available": False,
        "model": None,
        "detail": "Pixel is not enabled",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["status", "stream"])
@pytest.mark.parametrize(
    "error_type", [pixel.httpx.ConnectTimeout, pixel.httpx.ReadTimeout, pixel.httpx.ConnectError]
)
async def test_transport_diagnostics_log_type_without_sensitive_exception_text(
    monkeypatch, caplog, phase, error_type
):
    async def fail_enter(_self):
        raise error_type(f"private upstream details: Bearer {EDGE_KEY}")

    monkeypatch.setattr(FakeStreamContext, "__aenter__", fail_enter)
    monkeypatch.setattr(pixel.httpx, "AsyncClient", lambda **_kwargs: FakeClient(FakeResponse()))
    if phase == "status":
        result = await pixel.pixel_status()
        assert result["available"] is False
    else:
        body = pixel.ChatStreamRequest(
            chat_id="diagnostic-test", messages=[{"role": "user", "content": "hello"}]
        )
        with pytest.raises(HTTPException) as raised:
            await pixel.pixel_chat_stream(ConnectedRequest(), body)
        assert raised.value.status_code == 503
    assert error_type.__name__ in caplog.text
    assert EDGE_KEY not in caplog.text
    assert "private upstream details" not in caplog.text


@pytest.mark.asyncio
async def test_status_returns_only_fixed_projection():
    secret = "upstream-secret-must-not-appear"
    body = json.dumps({"data": [{"id": "pixel/default", "owned_by": secret}]}).encode()
    response = FakeResponse(chunks=[body])
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(response)):
        result = await pixel.pixel_status()
    assert result == {"available": True, "model": "pixel/default", "detail": "Owner agent ready"}
    assert secret not in json.dumps(result)


@pytest.mark.asyncio
async def test_status_projects_live_fixed_external_host_without_private_origin(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "external")
    values = {
        "LLM_BACKEND": "external",
        "ODS_MODEL_SWITCHBOARD": "observe",
        "EXTERNAL_LLM_PROVIDER": "openai-compatible",
        "EXTERNAL_LLM_MODEL": "Qwen3.5-9B-Q4_K_M.gguf",
    }
    monkeypatch.setattr(pixel, "read_live_env_value", lambda key, default="": values.get(key, default))

    async def loaded():
        return "Qwen3.5-9B-Q4_K_M.gguf"

    async def context(model):
        assert model == "Qwen3.5-9B-Q4_K_M.gguf"
        return 65536

    monkeypatch.setattr(pixel, "get_loaded_model", loaded)
    monkeypatch.setattr(pixel, "get_llama_context_size", context)
    body = json.dumps({"data": [{"id": "pixel/default"}]}).encode()
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(FakeResponse(chunks=[body]))):
        result = await pixel.pixel_status()

    assert result["runtime"] == {
        "source": "external-host", "model": "Qwen3.5-9B-Q4_K_M.gguf", "contextLength": 65536,
    }
    assert "host.lima.internal" not in json.dumps(result)
    assert "apiKey" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded_model", [None, "different-model"])
async def test_status_does_not_invent_external_host_identity_from_env(monkeypatch, loaded_model):
    monkeypatch.setenv("LLM_BACKEND", "external")
    values = {
        "LLM_BACKEND": "external",
        "ODS_MODEL_SWITCHBOARD": "observe",
        "EXTERNAL_LLM_PROVIDER": "openai-compatible",
        "EXTERNAL_LLM_MODEL": "Qwen3.5-9B-Q4_K_M.gguf",
    }
    monkeypatch.setattr(pixel, "read_live_env_value", lambda key, default="": values.get(key, default))

    async def loaded():
        return loaded_model

    monkeypatch.setattr(pixel, "get_loaded_model", loaded)
    body = json.dumps({"data": [{"id": "pixel/default"}]}).encode()
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(FakeResponse(chunks=[body]))):
        result = await pixel.pixel_status()
    assert "runtime" not in result


@pytest.mark.asyncio
async def test_external_host_identity_omits_unverified_context(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "external")
    values = {
        "LLM_BACKEND": "external",
        "ODS_MODEL_SWITCHBOARD": "observe",
        "EXTERNAL_LLM_PROVIDER": "openai-compatible",
        "EXTERNAL_LLM_MODEL": "Qwen3.5-9B-Q4_K_M.gguf",
    }
    monkeypatch.setattr(pixel, "read_live_env_value", lambda key, default="": values.get(key, default))

    async def loaded():
        return "Qwen3.5-9B-Q4_K_M.gguf"

    async def unknown_context(_model):
        return None

    monkeypatch.setattr(pixel, "get_loaded_model", loaded)
    monkeypatch.setattr(pixel, "get_llama_context_size", unknown_context)
    assert await pixel._verified_external_host_runtime({"status": "idle"}) == {
        "source": "external-host", "model": "Qwen3.5-9B-Q4_K_M.gguf",
    }


@pytest.mark.asyncio
async def test_status_projects_only_validated_active_remote_runtime(monkeypatch):
    async def active_remote_runtime(*_args, **_kwargs):
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

    monkeypatch.setattr(pixel, "request_agent_json", active_remote_runtime)
    body = json.dumps({"data": [{"id": "pixel/default"}]}).encode()
    with patch.object(
        pixel.httpx,
        "AsyncClient",
        return_value=FakeClient(FakeResponse(chunks=[body])),
    ):
        result = await pixel.pixel_status()

    assert result == {
        "available": True,
        "model": "pixel/default",
        "detail": "Owner agent ready",
        "runtime": {
            "source": "remote-provider",
            "model": "remote-owner-model",
            "contextLength": 131072,
            "maxTokens": 16384,
            "reasoning": False,
        },
    }


@pytest.mark.parametrize(
    "runtime",
    [
        {"source": "local-switchboard", "model": "local-model", "contextLength": True},
        {"source": "local-switchboard", "model": "local-model", "contextLength": 0},
        {"source": "local-switchboard", "model": "local-model", "contextLength": 65536,
         "apiKey": "must-not-project"},
        {"source": "external-host", "model": "Qwen.gguf", "apiKey": "must-not-project"},
        {"source": "external-host", "model": "Qwen.gguf", "contextLength": True},
        {"source": "external-host", "model": "http://private-origin"},
        {
            "source": "local",
            "model": "forged",
            "contextLength": 131072,
            "maxTokens": 16384,
            "reasoning": False,
        },
        {
            "source": "remote-provider",
            "model": "forged",
            "contextLength": 131072,
            "maxTokens": 16384,
            "reasoning": False,
            "apiKey": "must-not-project",
        },
        {
            "source": "remote-provider",
            "model": "forged",
            "contextLength": 131072,
            "maxTokens": 131073,
            "reasoning": False,
        },
    ],
)
def test_active_runtime_projection_rejects_untrusted_or_malformed_state(runtime):
    assert pixel._active_runtime_projection({"activeRuntime": runtime}) is None
    assert "must-not-project" not in json.dumps(
        pixel._active_runtime_projection({"activeRuntime": runtime})
    )


@pytest.mark.asyncio
async def test_status_projects_local_identity_even_for_adaptive_model(monkeypatch):
    runtime = {"source": "local-switchboard", "model": "Qwen3.6-35B-A3B-GGUF",
               "contextLength": 65536}

    async def local_status(*_args, **_kwargs):
        return {"status": "idle", "activeAgentViable": False, "activeRuntime": runtime}

    monkeypatch.setattr(pixel, "request_agent_json", local_status)
    body = json.dumps({"data": [{"id": "pixel/default"}]}).encode()
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(FakeResponse(chunks=[body]))):
        result = await pixel.pixel_status()
    assert result["available"] is True
    assert result["runtime"] == runtime
    assert result["modelSupport"]["tier"] == "adaptive"


@pytest.mark.asyncio
async def test_lemonade_model_drift_blocks_pixel_status_and_chat(monkeypatch):
    runtime = {"source": "local-switchboard", "model": "Qwen3.6-35B-A3B-GGUF",
               "contextLength": 65536}

    async def recorded_status(*_args, **_kwargs):
        return {"status": "idle", "activeRuntime": runtime}

    async def physical_model():
        return "Qwen3.5-2B-Q4_K_M"

    monkeypatch.setattr(pixel, "request_agent_json", recorded_status)
    monkeypatch.setattr(pixel, "read_live_env_value",
                        lambda key: "lemonade" if key == "LLM_BACKEND" else "")
    monkeypatch.setattr(pixel, "get_loaded_model", physical_model)
    with patch.object(pixel.httpx, "AsyncClient",
                      side_effect=AssertionError("stale route reached Pixel edge")):
        status = await pixel.pixel_status()
        body = pixel.ChatStreamRequest(
            chat_id="drift-test", messages=[{"role": "user", "content": "hello"}]
        )
        with pytest.raises(HTTPException) as raised:
            await pixel.pixel_chat_stream(ConnectedRequest(), body)
    assert status == {
        "available": False, "model": None, "state": "model_unavailable",
        "detail": pixel._MODEL_IDENTITY_DETAIL,
    }
    assert raised.value.status_code == 409
    assert raised.value.detail == pixel._MODEL_IDENTITY_DETAIL


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded", ["Qwen3.6-35B-A3B-GGUF", "Qwen3.6-35B-A3B-GGUF.gguf"])
async def test_matching_lemonade_model_keeps_pixel_available(monkeypatch, loaded):
    runtime = {"source": "local-switchboard", "model": "Qwen3.6-35B-A3B-GGUF",
               "contextLength": 65536}

    async def recorded_status(*_args, **_kwargs):
        return {"status": "idle", "activeRuntime": runtime}

    async def physical_model():
        return loaded

    monkeypatch.setattr(pixel, "request_agent_json", recorded_status)
    monkeypatch.setattr(pixel, "read_live_env_value",
                        lambda key: "lemonade" if key == "LLM_BACKEND" else "")
    monkeypatch.setattr(pixel, "get_loaded_model", physical_model)
    body = json.dumps({"data": [{"id": "pixel/default"}]}).encode()
    with patch.object(pixel.httpx, "AsyncClient",
                      return_value=FakeClient(FakeResponse(chunks=[body]))):
        status = await pixel.pixel_status()
    assert status["available"] is True
    assert status["runtime"] == runtime


@pytest.mark.asyncio
async def test_lemonade_probe_failure_fails_closed_without_logging_endpoint(monkeypatch, caplog):
    runtime = {"source": "local-switchboard", "model": "Qwen3.6-35B-A3B-GGUF",
               "contextLength": 65536}

    async def recorded_status(*_args, **_kwargs):
        return {"status": "idle", "activeRuntime": runtime}

    async def failed_probe():
        raise RuntimeError("private Lemonade origin and token")

    monkeypatch.setattr(pixel, "request_agent_json", recorded_status)
    monkeypatch.setattr(pixel, "read_live_env_value",
                        lambda key: "lemonade" if key == "LLM_BACKEND" else "")
    monkeypatch.setattr(pixel, "get_loaded_model", failed_probe)
    result = await pixel.pixel_status()
    assert result["available"] is False
    assert result["state"] == "model_unavailable"
    assert "private Lemonade" not in caplog.text


@pytest.mark.asyncio
async def test_non_lemonade_runtime_does_not_probe_lemonade_identity(monkeypatch):
    runtime = {"source": "local-switchboard", "model": "local-model",
               "contextLength": 65536}

    async def recorded_status(*_args, **_kwargs):
        return {"status": "idle", "activeRuntime": runtime}

    async def forbidden_probe():
        raise AssertionError("non-Lemonade route was probed")

    monkeypatch.setattr(pixel, "request_agent_json", recorded_status)
    monkeypatch.setattr(pixel, "read_live_env_value", lambda _key: "llama.cpp")
    monkeypatch.setattr(pixel, "get_loaded_model", forbidden_probe)
    body = json.dumps({"data": [{"id": "pixel/default"}]}).encode()
    with patch.object(pixel.httpx, "AsyncClient",
                      return_value=FakeClient(FakeResponse(chunks=[body]))):
        status = await pixel.pixel_status()
    assert status["available"] is True
    assert status["runtime"] == runtime


def test_active_runtime_projection_accepts_a_constrained_adaptive_context():
    runtime = {
        "source": "remote-provider",
        "model": "small-owner-model",
        "contextLength": 8192,
        "maxTokens": 1024,
        "reasoning": False,
    }
    assert pixel._active_runtime_projection({"activeRuntime": runtime}) == runtime


def test_active_remote_runtime_projects_only_a_valid_route_fingerprint():
    runtime = {"source": "remote-provider", "model": "same-model", "contextLength": 8192,
               "maxTokens": 1024, "reasoning": False, "routeFingerprint": "a" * 64}
    assert pixel._active_runtime_projection({"activeRuntime": runtime}) == runtime
    for invalid in (None, True, "private-url", "a" * 63, "a" * 64 + "\n", "A" * 64):
        assert pixel._active_runtime_projection({"activeRuntime": {**runtime, "routeFingerprint": invalid}}) is None
    assert pixel._active_runtime_projection({"activeRuntime": {**runtime, "baseUrl": "https://private.example"}}) is None


@pytest.mark.asyncio
async def test_status_projects_active_model_switch_without_touching_edge(monkeypatch):
    async def active_model_lifecycle(*_args, **_kwargs):
        return {
            "status": "idle",
            "lifecycleActive": True,
            "activeOperation": "model_activation",
            "activeTarget": "secret-model-name",
        }

    monkeypatch.setattr(pixel, "request_agent_json", active_model_lifecycle)
    with patch.object(
        pixel.httpx,
        "AsyncClient",
        side_effect=AssertionError("model switch readiness reached Pixel edge"),
    ):
        result = await pixel.pixel_status()

    assert result == {
        "available": False,
        "model": None,
        "state": "model_switching",
        "detail": pixel._MODEL_SWITCH_DETAIL,
    }
    assert "secret-model-name" not in json.dumps(result)


@pytest.mark.asyncio
async def test_pending_native_model_transaction_blocks_pixel_after_host_restart(monkeypatch):
    async def pending_transaction(*_args, **_kwargs):
        return {"status": "idle", "modelTransactionPending": True}

    monkeypatch.setattr(pixel, "request_agent_json", pending_transaction)
    with patch.object(
        pixel.httpx,
        "AsyncClient",
        side_effect=AssertionError("pending model transaction reached Pixel edge"),
    ):
        status = await pixel.pixel_status()
        assert status["available"] is False
        assert status["state"] == "model_switching"
        body = pixel.ChatStreamRequest.model_validate({
            "chat_id": "pending_model", "messages": [{"role": "user", "content": "hello"}],
        })
        with pytest.raises(HTTPException) as exc:
            await pixel.pixel_chat_stream(ConnectedRequest(), body)
        assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_status_keeps_adaptive_model_available_with_fixed_advisory(monkeypatch):
    async def adaptive_model(*_args, **_kwargs):
        return {
            "status": "idle",
            "activeAgentViable": False,
            "privateModelPath": "/secret/model.gguf",
        }

    monkeypatch.setattr(pixel, "request_agent_json", adaptive_model)
    body = json.dumps({"data": [{"id": "pixel/default"}]}).encode()
    with patch.object(
        pixel.httpx,
        "AsyncClient",
        return_value=FakeClient(FakeResponse(chunks=[body])),
    ):
        result = await pixel.pixel_status()

    assert result == {
        "available": True,
        "model": "pixel/default",
        "detail": "Owner agent ready",
        "modelSupport": {
            "tier": "adaptive",
            "detail": pixel._MODEL_ADAPTIVE_DETAIL,
        },
    }
    assert "secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_lifecycle_probe_failure_does_not_falsely_disable_pixel(monkeypatch):
    async def failed_model_lifecycle(*_args, **_kwargs):
        raise pixel.AgentClientError("unreachable")

    monkeypatch.setattr(pixel, "request_agent_json", failed_model_lifecycle)
    assert await pixel._model_activation_in_progress() is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(status=500, chunks=[b"secret"]),
        FakeResponse(content_type="text/plain", chunks=[b"secret"]),
        FakeResponse(chunks=[b"x" * (64 * 1024 + 1)]),
        FakeResponse(chunks=[b"not-json"]),
    ],
)
async def test_status_errors_are_bounded_and_sanitized(response):
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(response)):
        result = await pixel.pixel_status()
    assert result["available"] is False
    assert "secret" not in json.dumps(result)
    assert "not-json" not in json.dumps(result)


@pytest.mark.asyncio
async def test_chat_forwards_exact_body_and_narrow_edge_key_only():
    capture = {}
    upstream = FakeResponse(
        content_type="text/event-stream; charset=utf-8",
        chunks=[b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n', b"data: [DONE]\n\n"],
    )
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "conversation_1", "messages": [{"role": "user", "content": "hello"}]}
    )
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(upstream, capture)):
        response = await pixel.pixel_chat_stream(ConnectedRequest(), body)
        streamed = await stream_body(response)
    assert streamed.count(b"data: [DONE]") == 1
    assert capture["method"] == "POST"
    assert capture["url"] == "http://pixel-edge:9595/v1/chat/completions"
    assert capture["json"]["messages"][0]["role"] == "system"
    assert '"Portal"' in capture["json"]["messages"][0]["content"]
    assert capture["json"]["messages"][1:] == [{"role": "user", "content": "hello"}]
    assert {key: value for key, value in capture["json"].items() if key != "messages"} == {
        "model": "pixel/default",
        "stream": True,
        "user": "conversation_1",
    }
    assert capture["headers"]["Authorization"] == f"Bearer {EDGE_KEY}"
    assert "dashboard-test-key" not in json.dumps(capture)
    assert pixel_runtime_state._local_pixel_stream_active() is False


@pytest.mark.asyncio
async def test_chat_keeps_silent_local_inference_stream_alive_without_faking_answer(monkeypatch):
    first = b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
    done = b"data: [DONE]\n\n"

    class SlowResponse(FakeResponse):
        async def aiter_bytes(self):
            yield first
            await asyncio.sleep(0.08)
            yield done

    monkeypatch.setattr(pixel, "_STREAM_KEEPALIVE_SECONDS", 0.02)
    monkeypatch.setattr(pixel, "_CLIENT_DISCONNECT_POLL_SECONDS", 0.005)
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "slow_cpu", "messages": [{"role": "user", "content": "hello"}]}
    )
    upstream = SlowResponse(content_type="text/event-stream")
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(upstream)):
        response = await pixel.pixel_chat_stream(ConnectedRequest(), body)
        streamed = await stream_body(response)

    assert streamed.startswith(first)
    assert streamed.endswith(done)
    assert pixel._STREAM_KEEPALIVE in streamed[len(first):-len(done)]
    assert streamed.count(b"data: [DONE]") == 1
    assert pixel_runtime_state._local_pixel_stream_active() is False


@pytest.mark.asyncio
async def test_chat_keepalive_before_first_upstream_byte_is_only_a_comment(monkeypatch):
    class SlowFirstResponse(FakeResponse):
        async def aiter_bytes(self):
            await asyncio.sleep(0.08)
            yield b'data: {"choices":[{"delta":{"content":"ready"}}]}\n\n'
            yield b'data: [DONE]\n\n'

    monkeypatch.setattr(pixel, "_STREAM_KEEPALIVE_SECONDS", 0.02)
    monkeypatch.setattr(pixel, "_CLIENT_DISCONNECT_POLL_SECONDS", 0.005)
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "slow_first_byte", "messages": [{"role": "user", "content": "hello"}]}
    )
    upstream = SlowFirstResponse(content_type="text/event-stream")
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(upstream)):
        response = await pixel.pixel_chat_stream(ConnectedRequest(), body)
        streamed = await stream_body(response)

    assert streamed.startswith(pixel._STREAM_KEEPALIVE)
    assert b'ready' in streamed
    assert streamed.endswith(b'data: [DONE]\n\n')


@pytest.mark.asyncio
async def test_chat_keepalive_never_splits_an_upstream_sse_line(monkeypatch):
    first = b'data: {"choices":[{"delta":{"content":"par'
    last = b'tial"}}]}\n\n'
    done = b'data: [DONE]\n\n'

    class FragmentedResponse(FakeResponse):
        async def aiter_bytes(self):
            yield first
            await asyncio.sleep(0.08)
            yield last
            await asyncio.sleep(0.08)
            yield done

    monkeypatch.setattr(pixel, "_STREAM_KEEPALIVE_SECONDS", 0.02)
    monkeypatch.setattr(pixel, "_CLIENT_DISCONNECT_POLL_SECONDS", 0.005)
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "fragmented_cpu", "messages": [{"role": "user", "content": "hello"}]}
    )
    upstream = FragmentedResponse(content_type="text/event-stream")
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(upstream)):
        response = await pixel.pixel_chat_stream(ConnectedRequest(), body)
        streamed = await stream_body(response)

    assert streamed.startswith(first + last)
    assert pixel._STREAM_KEEPALIVE in streamed[len(first + last):-len(done)]
    assert streamed.endswith(done)


@pytest.mark.asyncio
async def test_chat_rejects_before_opening_edge_when_stream_capacity_is_full(monkeypatch):
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "capacity", "messages": [{"role": "user", "content": "hello"}]}
    )
    monkeypatch.setattr(pixel, "try_begin_pixel_stream", lambda: False)
    with patch.object(pixel.httpx, "AsyncClient", side_effect=AssertionError("edge must not be opened")):
        with pytest.raises(HTTPException) as exc_info:
            await pixel.pixel_chat_stream(ConnectedRequest(), body)
    assert exc_info.value.status_code == 429
    assert "capacity" in exc_info.value.detail


@pytest.mark.asyncio
async def test_chat_preserves_the_host_authored_recovery_marker_verbatim():
    marker = (
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
        b'"pixel":{"schemaVersion":1,"recovery":"clean-context",'
        b'"reason":"operations-unavailable-zero-submissions"}}\n\n'
    )
    upstream = FakeResponse(
        content_type="text/event-stream",
        chunks=[marker, b"data: [DONE]\n\n"],
    )
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "conversation_1", "messages": [{"role": "user", "content": "hello"}]}
    )
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(upstream)):
        response = await pixel.pixel_chat_stream(ConnectedRequest(), body)
        streamed = await stream_body(response)
    assert streamed == marker + b"data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_chat_is_rejected_before_edge_during_model_activation(monkeypatch):
    async def active_model_lifecycle(*_args, **_kwargs):
        return {"lifecycleActive": True, "activeOperation": "model_activation"}

    monkeypatch.setattr(pixel, "request_agent_json", active_model_lifecycle)
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "c1", "messages": [{"role": "user", "content": "hello"}]}
    )
    with patch.object(
        pixel.httpx,
        "AsyncClient",
        side_effect=AssertionError("model switch turn reached Pixel edge"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await pixel.pixel_chat_stream(ConnectedRequest(), body)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == pixel._MODEL_SWITCH_DETAIL


@pytest.mark.asyncio
async def test_chat_rechecks_and_allows_adaptive_model(monkeypatch):
    async def adaptive_model(*_args, **_kwargs):
        return {"status": "idle", "activeAgentViable": False}

    monkeypatch.setattr(pixel, "request_agent_json", adaptive_model)
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "c1", "messages": [{"role": "user", "content": "hello"}]}
    )
    upstream = FakeResponse(
        content_type="text/event-stream",
        chunks=[b"data: [DONE]\n\n"],
    )
    with patch.object(
        pixel.httpx,
        "AsyncClient",
        return_value=FakeClient(upstream),
    ):
        response = await pixel.pixel_chat_stream(ConnectedRequest(), body)
        streamed = await stream_body(response)

    assert streamed == b"data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_accepted_chat_marks_runtime_active_until_stream_closes():
    upstream = FakeResponse(
        content_type="text/event-stream",
        chunks=[b"data: [DONE]\n\n"],
    )
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "c1", "messages": [{"role": "user", "content": "hello"}]}
    )
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(upstream)):
        response = await pixel.pixel_chat_stream(ConnectedRequest(), body)
        assert pixel_stream_active() is True
        assert await stream_body(response) == b"data: [DONE]\n\n"
    assert pixel_runtime_state._local_pixel_stream_active() is False


def test_pixel_runtime_activity_accepts_only_exact_content_free_projection(monkeypatch):
    class ActivityResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        content = b'{"active":true,"streams":2}'

        def json(self):
            return {"active": True, "streams": 2}

    class ActivityClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url, **kwargs):
            assert url == "http://pixel-edge:9595/v1/activity"
            assert kwargs["headers"]["Authorization"] == f"Bearer {EDGE_KEY}"
            return ActivityResponse()

    monkeypatch.setattr(pixel_runtime_state.httpx, "Client", lambda **_kwargs: ActivityClient())
    assert pixel_runtime_state._pixel_edge_stream_active() is True


def test_pixel_runtime_activity_fails_open_on_ambiguous_projection(monkeypatch):
    class ActivityResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        content = b'{"active":true,"streams":1,"chat":"secret"}'

        def json(self):
            return {"active": True, "streams": 1, "chat": "secret"}

    class ActivityClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, *_args, **_kwargs):
            return ActivityResponse()

    monkeypatch.setattr(pixel_runtime_state.httpx, "Client", lambda **_kwargs: ActivityClient())
    assert pixel_runtime_state._pixel_edge_stream_active() is False


@pytest.mark.asyncio
async def test_silent_upstream_is_cancelled_when_dashboard_client_disconnects():
    upstream = SilentResponse()
    calls = []
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "c1", "messages": [{"role": "user", "content": "hello"}]}
    )
    with patch.object(
        pixel.httpx,
        "AsyncClient",
        return_value=CancelAwareClient(upstream, calls),
    ):
        response = await pixel.pixel_chat_stream(DisconnectedRequest(), body)
        streamed = await asyncio.wait_for(stream_body(response), timeout=2)
    assert streamed == b""
    assert upstream.cancelled.is_set()
    assert [call["url"] for call in calls] == [
        "http://pixel-edge:9595/v1/chat/completions",
        "http://pixel-edge:9595/v1/chat/cancel",
    ]
    assert calls[1]["json"] == {"user": "c1"}
    assert calls[1]["headers"]["Authorization"] == f"Bearer {EDGE_KEY}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "upstream",
    [
        FakeResponse(status=500, content_type="text/event-stream", chunks=[b"secret upstream body"]),
        FakeResponse(content_type="text/plain", chunks=[b"secret upstream body"]),
    ],
)
async def test_stream_rejections_never_reflect_upstream_body(upstream):
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "c1", "messages": [{"role": "user", "content": "hello"}]}
    )
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(upstream)):
        with pytest.raises(HTTPException) as exc_info:
            await pixel.pixel_chat_stream(ConnectedRequest(), body)
    assert exc_info.value.status_code == 502
    assert "secret upstream body" not in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_stream_projects_edge_transition_as_actionable_conflict_without_reflection():
    upstream = FakeResponse(
        status=409,
        content_type="application/json",
        chunks=[b'{"error":"pixel_transition_in_progress","private":"secret upstream body"}'],
    )
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "c1", "messages": [{"role": "user", "content": "hello"}]}
    )
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(upstream)):
        with pytest.raises(HTTPException) as exc_info:
            await pixel.pixel_chat_stream(ConnectedRequest(), body)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == pixel._MODEL_SWITCH_DETAIL
    assert "secret upstream body" not in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_stream_line_limit_fails_closed():
    upstream = FakeResponse(
        content_type="text/event-stream",
        chunks=[b"data: " + b"x" * (1024 * 1024 + 1)],
    )
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "c1", "messages": [{"role": "user", "content": "hello"}]}
    )
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(upstream)):
        response = await pixel.pixel_chat_stream(ConnectedRequest(), body)
        streamed = await stream_body(response)
    assert b"safety limit" in streamed
    assert streamed.endswith(b"data: [DONE]\n\n")


@pytest.mark.asyncio
async def test_unterminated_done_marker_is_forwarded_as_one_framed_done():
    calls = []
    upstream = FakeResponse(
        content_type="text/event-stream",
        chunks=[b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n', b"data: [DONE]"],
    )
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "c1", "messages": [{"role": "user", "content": "hello"}]}
    )
    with patch.object(pixel.httpx, "AsyncClient", return_value=CancelAwareClient(upstream, calls)):
        response = await pixel.pixel_chat_stream(ConnectedRequest(), body)
        streamed = await stream_body(response)
    assert streamed.count(b"data: [DONE]") == 1
    assert streamed.endswith(b"data: [DONE]\n")
    # A real upstream terminator means the completed edge run is never
    # redundantly cancelled during release.
    assert [call["url"] for call in calls] == ["http://pixel-edge:9595/v1/chat/completions"]


@pytest.mark.asyncio
async def test_unterminated_partial_line_cannot_swallow_the_terminal_done():
    upstream = FakeResponse(
        content_type="text/event-stream",
        chunks=[b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n', b'data: {"partial"'],
    )
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "c1", "messages": [{"role": "user", "content": "hello"}]}
    )
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(upstream)):
        response = await pixel.pixel_chat_stream(ConnectedRequest(), body)
        streamed = await stream_body(response)
    assert streamed.endswith(b'data: {"partial"\ndata: [DONE]\n\n')


@pytest.mark.asyncio
async def test_content_after_done_never_emits_a_second_terminal_done():
    upstream = FakeResponse(
        content_type="text/event-stream",
        chunks=[b"data: [DONE]\n\n", b"data: " + b"x" * (1024 * 1024 + 1) + b"\n"],
    )
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "c1", "messages": [{"role": "user", "content": "hello"}]}
    )
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(upstream)):
        response = await pixel.pixel_chat_stream(ConnectedRequest(), body)
        streamed = await stream_body(response)
    assert b"safety limit" in streamed
    assert streamed.count(b"data: [DONE]") == 1


@pytest.mark.asyncio
async def test_stream_handles_split_utf8_without_decoding_or_corruption():
    value = "hello 😀".encode("utf-8")
    upstream = FakeResponse(
        content_type="text/event-stream",
        chunks=[b"data: " + value[:8], value[8:] + b"\n\n", b"data: [DONE]\n\n"],
    )
    body = pixel.ChatStreamRequest.model_validate(
        {"chat_id": "c1", "messages": [{"role": "user", "content": "hello"}]}
    )
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(upstream)):
        response = await pixel.pixel_chat_stream(ConnectedRequest(), body)
        streamed = await stream_body(response)
    assert value in streamed
    assert streamed.count(b"data: [DONE]") == 1

@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["active", "terminal", "unknown"])
async def test_chat_activity_forwards_exact_chat_and_projects_no_credentials(state):
    capture = {}
    response = FakeResponse(chunks=[json.dumps({"state": state}).encode()])
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(response, capture)) as client:
        result = await pixel.pixel_chat_activity(pixel.ChatCancelRequest(chat_id="opaque_chat_1"))
    assert result == {"state": state}
    assert capture["method"] == "POST"
    assert capture["url"] == "http://pixel-edge:9595/v1/chat/activity"
    assert capture["json"] == {"user": "opaque_chat_1"}
    assert capture["headers"]["Authorization"] == f"Bearer {EDGE_KEY}"
    assert client.call_args.kwargs["trust_env"] is False
    assert client.call_args.kwargs["follow_redirects"] is False
    assert EDGE_KEY not in json.dumps(result)

@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    FakeResponse(status=500), FakeResponse(content_type="text/plain"),
    FakeResponse(chunks=[b'{"active":true,"streams":9}']),
    FakeResponse(chunks=[b'{"state":"active","secret":"hidden"}']),
    FakeResponse(chunks=[b'{"state":true}']), FakeResponse(chunks=[b'x' * 1025]),
])
async def test_chat_activity_ambiguity_is_unknown_not_global_activity(response):
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(response)):
        assert await pixel.pixel_chat_activity(pixel.ChatCancelRequest(chat_id="opaque_chat_1")) == {"state": "unknown"}

@pytest.mark.asyncio
async def test_chat_activity_transport_failure_is_sanitized():
    with patch.object(pixel.httpx, "AsyncClient", side_effect=pixel.httpx.ConnectError("private credential")):
        assert await pixel.pixel_chat_activity(pixel.ChatCancelRequest(chat_id="opaque_chat_1")) == {"state": "unknown"}
