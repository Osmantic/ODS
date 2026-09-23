"""ODS model-router contract tests (Switchboard PR 3). No sockets: the
upstream is an httpx.MockTransport and state/endpoints are temp files."""

from __future__ import annotations

import base64
import asyncio
import hashlib
import hmac
import importlib
import json
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))


@pytest.fixture()
def router(tmp_path, monkeypatch):
    """Fresh app instance wired to temp state/endpoints and a mock upstream."""
    state_path = tmp_path / "model-state.json"
    endpoints_path = tmp_path / "endpoints.json"
    endpoints_path.write_text(json.dumps({
        "endpoints": [
            {"id": "llama-server-default", "baseUrl": "http://upstream:8080"},
            {"id": "keyed", "baseUrl": "http://keyed:9000", "apiKeyEnv": "KEYED_API_KEY"},
        ]
    }), encoding="utf-8")

    import app.main as mod
    mod = importlib.reload(mod)
    monkeypatch.setattr(mod, "STATE_PATH", state_path)
    monkeypatch.setattr(mod, "ENDPOINTS_PATH", endpoints_path)
    monkeypatch.setattr(mod, "INTERNAL_KEY", "internal-secret")
    monkeypatch.setattr(mod, "PROBE_KEY", "probe-secret")
    mod._endpoints_cache.update({"mtime": None, "endpoints": {}})
    mod._state_cache.update({"mtime": None, "doc": None})
    mod._evidence.clear()

    calls: list[dict] = []

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        calls.append({
            "url": str(request.url),
            "model": body.get("model"),
            "auth": request.headers.get("authorization"),
            "stream": bool(body.get("stream")),
        })
        if body.get("stream"):
            sse = (
                b'data: {"id":"c1","model":"Concrete.gguf","choices":[{"delta":{"content":"hi"}}]}\n\n'
                b"data: [DONE]\n\n"
            )
            return httpx.Response(200, content=sse,
                                  headers={"content-type": "text/event-stream",
                                           "x-lemonade-route": "route-a"})
        return httpx.Response(200, json={
            "id": "c1", "model": "Concrete.gguf",
            "choices": [{"message": {"role": "assistant", "content": "hello"}}],
        }, headers={"x-lemonade-route": "route-a"})

    def write_state(runtime="Concrete.gguf", endpoint="llama-server-default",
                    queue=False, route_seq=7, seq=None, mutate=None):
        doc = {
            "schema": "ods.model-state.v1", "seq": route_seq, "routeSeq": route_seq,
            "operation": None, "desired": {"catalogId": "concrete"},
            "active": {
                "routeSeq": route_seq, "catalogId": "concrete",
                "runtimeModelId": runtime, "publicModel": "ods/current",
                "backend": {"kind": "llama-server", "endpointId": endpoint,
                            "nativeRoute": None},
                "contextLength": 4096,
                "capabilities": {"chat": True, "tools": False, "vision": False,
                                 "agentViable": False},
                "verifiedAt": "2026-07-20T00:00:00Z",
                "proof": {"identity": runtime, "completion": True},
            },
            "history": [],
            "availability": {"mode": "queue" if queue else "serve_active",
                             "queueDeadline": None},
        }
        doc["seq"] = route_seq if seq is None else seq
        if mutate:
            mutate(doc)
        # Match Switchboard's publication boundary so concurrent requests never
        # observe the destination between truncation and a completed write.
        staged = state_path.with_name(f".{state_path.name}.{uuid.uuid4().hex}.tmp")
        staged.write_text(json.dumps(doc), encoding="utf-8")
        staged.replace(state_path)
        mod._state_cache["mtime"] = None

    client = TestClient(mod.app)
    client.__enter__()
    mod.app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(upstream_handler)
    )
    mod._inflight = 0
    mod._waiting = 0
    mod._swap_gate = None
    yield mod, client, write_state, calls
    client.__exit__(None, None, None)


def _signed_marker(probe_id: str, key: str = "probe-secret") -> str:
    sig = base64.urlsafe_b64encode(
        hmac.new(key.encode(), probe_id.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return f"[ODS_PROBE id={probe_id} sig={sig}]"


def test_internal_key_falls_back_to_dashboard_api_key(monkeypatch):
    monkeypatch.delenv("ODS_ROUTER_INTERNAL_KEY", raising=False)
    monkeypatch.setenv("DASHBOARD_API_KEY", "dashboard-secret")
    import app.main as mod

    mod = importlib.reload(mod)

    assert mod.INTERNAL_KEY == "dashboard-secret"


class _ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks, error=None, started=None, release=None):
        self.chunks = chunks
        self.error = error
        self.started = started
        self.release = release

    async def __aiter__(self):
        for index, chunk in enumerate(self.chunks):
            yield chunk
            if index == 0 and self.started is not None:
                self.started.set()
                await asyncio.to_thread(self.release.wait, 5)
        if self.error is not None:
            raise self.error

    async def aclose(self):
        return None


def _set_stream_upstream(mod, chunks, *, model_error=None, started=None,
                         release=None):
    def handler(_request):
        return httpx.Response(
            200,
            stream=_ChunkedStream(chunks, model_error, started, release),
            headers={"content-type": "text/event-stream"},
        )

    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))


class _RecordingTelemetry:
    def __init__(self, *, raises=False):
        self.events = []
        self.raises = raises

    def emit(self, event):
        if self.raises:
            raise RuntimeError("telemetry unavailable")
        self.events.append(event)
        return True

    async def stop(self):
        return None


class TestForwarding:
    def test_alias_rewritten_in_and_out(self, router):
        mod, client, write_state, calls = router
        write_state()
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200
        assert calls[-1]["model"] == "Concrete.gguf"
        body = resp.json()
        assert body["model"] == "ods/current"
        assert resp.headers["X-ODS-Requested-Model"] == "ods/current"
        assert resp.headers["X-ODS-Routed-Model"] == "Concrete.gguf"
        assert resp.headers["X-ODS-Route-Seq"] == "7"
        assert resp.headers["X-Lemonade-Route"] == "route-a"

    def test_chat_template_artifacts_stripped_from_json_content(self, router):
        mod, client, write_state, calls = router
        write_state()

        def handler(_request):
            return httpx.Response(200, json={
                "id": "c1",
                "model": "Concrete.gguf",
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "O<|im_start|>assistant<|im_end|>DSVAL",
                    },
                }],
            })

        asyncio.run(mod.app.state.http.aclose())
        mod.app.state.http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )

        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": "hi"}],
        })

        assert resp.status_code == 200
        body = resp.json()
        assert body["choices"][0]["message"]["content"] == "ODSVAL"
        assert "<|im_start|>" not in resp.text
        assert body["model"] == "ods/current"

    def test_sse_chunks_restore_alias(self, router):
        mod, client, write_state, calls = router
        write_state()
        with client.stream("POST", "/v1/chat/completions", json={
            "model": "default", "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        }) as resp:
            assert resp.status_code == 200
            raw = b"".join(resp.iter_bytes())
        assert b'"model": "default"' in raw or b'"model":"default"' in raw
        assert b"Concrete.gguf" not in raw
        assert b"[DONE]" in raw

    def test_local_tool_stream_uses_completed_backend_decision(self, router):
        mod, client, write_state, _calls = router
        write_state(mutate=lambda state: state["active"]["backend"].update(
            kind="lemonade"))
        sent = []

        def handler(request):
            body = json.loads(request.content)
            sent.append(body)
            # This backend fails while diffing streamed calls, but its
            # completed Chat response contains the exact tool decision.
            if body["stream"]:
                return httpx.Response(500, json={"error": {
                    "message": "Invalid diff: now finding less tool calls!"}})
            return httpx.Response(200, json={
                "id": "backend-call", "object": "chat.completion",
                "created": 123, "model": "Concrete.gguf",
                "choices": [{"index": 0, "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "call-1", "type": "function",
                                    "function": {"name": "lookup",
                                                 "arguments": '{"key":"ok"}'}}],
                }, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 8,
                          "total_tokens": 28},
            })

        asyncio.run(mod.app.state.http.aclose())
        mod.app.state.http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler))
        response = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": "look up ok"}],
            "tools": [{"type": "function", "function": {
                "name": "lookup", "parameters": {"type": "object"}}}],
        })

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert sent[0]["stream"] is False
        assert "stream_options" not in sent[0]
        assert sent[0]["tools"][0]["function"]["name"] == "lookup"
        frames = [item for item in response.text.split("\n\n") if item]
        assert frames[-1] == "data: [DONE]"
        chunks = [json.loads(item.removeprefix("data: ")) for item in frames[:-1]]
        assert all(chunk["model"] == "ods/current" for chunk in chunks)
        assert chunks[0]["choices"][0]["delta"]["tool_calls"] == [{
            "id": "call-1", "type": "function",
            "function": {"name": "lookup", "arguments": '{"key":"ok"}'},
            "index": 0,
        }]
        assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
        assert chunks[-1]["usage"]["total_tokens"] == 28

    def test_local_tool_stream_reports_lemonade_context_error_not_invalid_completion(self, router):
        mod, client, write_state, _calls = router
        write_state(mutate=lambda state: state["active"]["backend"].update(
            kind="lemonade"))

        def handler(_request):
            return httpx.Response(200, json={"error": {
                "message": "llama-server request failed", "details": {
                    "status_code": 400, "response": {"error": {
                        "type": "exceed_context_size_error", "n_ctx": 8192,
                        "n_prompt_tokens": 9000,
                        "message": "untrusted backend detail"}}}}})

        asyncio.run(mod.app.state.http.aclose())
        mod.app.state.http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler))
        response = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [{"type": "function", "function": {
                "name": "lookup", "parameters": {"type": "object"}}}],
        })

        assert response.status_code == 400
        assert response.json()["error"] == {
            "message": "Request (9000 tokens) exceeds the available context size (8192 tokens)",
            "type": "exceed_context_size_error", "code": "400",
            "n_ctx": 8192, "n_prompt_tokens": 9000,
        }
        assert "untrusted backend detail" not in response.text
        assert "[DONE]" not in response.text

    def test_complete_native_markup_uses_only_advertised_valid_tool(self, router):
        mod, client, write_state, _calls = router
        write_state(mutate=lambda state: state["active"]["backend"].update(
            kind="lemonade"))
        native = (
            "<tool_call>\n<function=pixel_ods_python_library_proposal>\n"
            "<parameter=repository>\nhttps://github.com/pypa/packaging\n</parameter>\n"
            "<parameter=serviceId>\npackaging-core-utilities\n</parameter>\n"
            "<parameter=name>\npackaging-core-utilities\n</parameter>\n"
            "<parameter=pythonVersion>\n3.10\n</parameter>\n"
            '<parameter=pythonImports>\n["packaging.version"]\n</parameter>\n'
            "</function>\n</tool_call>"
        )
        def handler(_request):
            return httpx.Response(200, json={
                "id": "native-call", "model": "Concrete.gguf",
                "choices": [{"index": 0, "message": {
                    "role": "assistant", "content": native,
                }, "finish_reason": "stop"}],
            })
        asyncio.run(mod.app.state.http.aclose())
        mod.app.state.http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler))
        response = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True,
            "messages": [{"role": "user", "content": "propose packaging"}],
            "tools": [{"type": "function", "function": {
                "name": "pixel_ods_python_library_proposal",
                "parameters": {"type": "object", "additionalProperties": False,
                    "required": ["repository", "serviceId", "name",
                                 "pythonVersion", "pythonImports"],
                    "properties": {
                        "repository": {"type": "string"},
                        "serviceId": {"type": "string"},
                        "name": {"type": "string"},
                        "pythonVersion": {"type": "string", "pattern": r"^3\.10$"},
                        "pythonImports": {"type": "array", "minItems": 1,
                                          "items": {"type": "string"}},
                    }},
            }}],
        })
        assert response.status_code == 200
        chunks = [json.loads(frame.removeprefix("data: "))
                  for frame in response.text.split("\n\n")
                  if frame and frame != "data: [DONE]"]
        delta = chunks[0]["choices"][0]["delta"]
        assert delta["content"] is None
        assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
        call = delta["tool_calls"][0]
        assert call["function"]["name"] == "pixel_ods_python_library_proposal"
        assert json.loads(call["function"]["arguments"]) == {
            "repository": "https://github.com/pypa/packaging",
            "serviceId": "packaging-core-utilities",
            "name": "packaging-core-utilities",
            "pythonVersion": "3.10",
            "pythonImports": ["packaging.version"],
        }

    @pytest.mark.parametrize("content,choice,offered,parallel", [
        ("prefix <tool_call>\n<function=lookup>\n<parameter=key>\nok\n</parameter>\n</function>\n</tool_call>", "auto", True, True),
        ("<tool_call>\n<function=lookup>\n<parameter=key>\nok\n</parameter>\n</function>", "auto", True, True),
        ("<tool_call>\n<function=pixel_ods_web_fetch>\n<parameter=key>\nok\n</parameter>\n</function>\n</tool_call>", "auto", True, True),
        ("<tool_call>\n<function=lookup>\n<parameter=key>\nok\n</parameter>\n</function>\n</tool_call>", "none", True, True),
        ("<tool_call>\n<function=lookup>\n<parameter=key>\nok\n</parameter>\n</function>\n</tool_call>", "auto", False, True),
        ("<tool_call>\n<function=lookup>\n<parameter=key>\nok\n</parameter>\n<parameter=key>\nagain\n</parameter>\n</function>\n</tool_call>", "auto", True, True),
        ("<tool_call>\n<function=lookup>\n<parameter=key>\nok\n</parameter>\n</function>\n</tool_call>\nextra", "auto", True, True),
    ])
    def test_native_markup_rejects_untrusted_or_incomplete_text(
        self, router, content, choice, offered, parallel,
    ):
        mod, _client, _write_state, _calls = router
        completion = {"choices": [{"message": {"role": "assistant",
            "content": content}, "finish_reason": "stop"}]}
        request = {"tool_choice": choice, "parallel_tool_calls": parallel,
                   "tools": ([{"type": "function", "function": {
                       "name": "lookup", "parameters": {"type": "object",
                       "additionalProperties": False, "required": ["key"],
                       "properties": {"key": {"type": "string"}}}}}]
                             if offered else [])}
        assert mod._normalize_native_tool_markup(completion, request) is False
        assert completion["choices"][0]["message"] == {
            "role": "assistant", "content": content,
        }

    def test_native_markup_validates_arguments_and_parallel_choice(self, router):
        mod, _client, _write_state, _calls = router
        call = ("<tool_call>\n<function=lookup>\n"
                "<parameter=key>\nok\n</parameter>\n</function>\n</tool_call>")
        request = {"tools": [{"type": "function", "function": {
            "name": "lookup", "parameters": {"type": "object",
                "additionalProperties": False, "required": ["key"],
                "properties": {"key": {"type": "integer"}}}}}],
            "parallel_tool_calls": False}
        completion = {"choices": [{"message": {"role": "assistant",
            "content": call}, "finish_reason": "stop"}]}
        assert mod._normalize_native_tool_markup(completion, request) is False
        request["tools"][0]["function"]["parameters"]["properties"]["key"] = {"type": "string"}
        completion["choices"][0]["message"]["content"] = call + "\n" + call
        assert mod._normalize_native_tool_markup(completion, request) is False
        request["parallel_tool_calls"] = True
        assert mod._normalize_native_tool_markup(completion, request) is True
        assert len(completion["choices"][0]["message"]["tool_calls"]) == 2

    def test_native_markup_rejects_reference_schema_and_forced_other_tool(self, router):
        mod, _client, _write_state, _calls = router
        native = ("<tool_call>\n<function=lookup>\n"
                  "<parameter=key>\nok\n</parameter>\n</function>\n</tool_call>")
        completion = {"choices": [{"message": {"role": "assistant",
            "content": native}, "finish_reason": "stop"}]}
        tool = {"type": "function", "function": {"name": "lookup",
            "parameters": {"type": "object", "required": ["key"],
                "properties": {"key": {"$ref": "https://example.invalid/schema"}}}}}
        request = {"tools": [tool]}
        assert mod._normalize_native_tool_markup(completion, request) is False
        assert completion["choices"][0]["message"]["content"] == native
        tool["function"]["parameters"]["properties"]["key"] = {"type": "string"}
        request["tool_choice"] = {"type": "function", "function": {"name": "other"}}
        assert mod._normalize_native_tool_markup(completion, request) is False

    def test_invalid_complete_native_name_gets_one_completion_repair(self, router):
        mod, client, write_state, _calls = router
        write_state(mutate=lambda state: state["active"]["backend"].update(
            kind="lemonade"))
        sent = []
        def handler(request):
            body = json.loads(request.content)
            sent.append(body)
            if len(sent) == 1:
                message = {"role": "assistant", "content": (
                    "<tool_call>\n<function=pixel_ods_web_fetch>\n"
                    "<parameter=url>\nhttps://example.org\n</parameter>\n"
                    "</function>\n</tool_call>")}
                reason = "stop"
            else:
                message = {"role": "assistant", "content": None,
                    "tool_calls": [{"id": "call-1", "type": "function",
                        "function": {"name": "web_fetch",
                                     "arguments": '{"url":"https://example.org"}'}}]}
                reason = "tool_calls"
            return httpx.Response(200, json={"model": "Concrete.gguf",
                "choices": [{"index": 0, "message": message,
                             "finish_reason": reason}]})
        asyncio.run(mod.app.state.http.aclose())
        mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        response = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True,
            "messages": [{"role": "user", "content": "fetch the URL"}],
            "tools": [{"type": "function", "function": {"name": "web_fetch",
                "parameters": {"type": "object", "required": ["url"],
                    "properties": {"url": {"type": "string"}}}}}],
        })
        assert response.status_code == 200
        assert len(sent) == 2 and all(body["stream"] is False for body in sent)
        assert sent[1]["messages"][:-1] == sent[0]["messages"]
        feedback = sent[1]["messages"][-1]["content"]
        assert sent[1]["messages"][-1]["role"] == "user"
        assert "pixel_ods_web_fetch" in feedback and "web_fetch" in feedback
        assert sent[1]["tools"] == sent[0]["tools"]
        chunks = [json.loads(frame.removeprefix("data: "))
                  for frame in response.text.split("\n\n")
                  if frame and frame != "data: [DONE]"]
        assert chunks[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "web_fetch"

    def test_second_invalid_native_decision_returns_typed_error(self, router):
        mod, client, write_state, _calls = router
        write_state()
        sent = []
        native = ("<tool_call>\n<function=missing_tool>\n"
                  "<parameter=key>\nok\n</parameter>\n</function>\n</tool_call>")
        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"model": "Concrete.gguf",
                "choices": [{"index": 0, "message": {"role": "assistant",
                    "content": native}, "finish_reason": "stop"}]})
        asyncio.run(mod.app.state.http.aclose())
        mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        response = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True,
            "messages": [{"role": "user", "content": "test"}],
            "tools": [{"type": "function", "function": {"name": "lookup",
                "parameters": {"type": "object", "properties": {
                    "key": {"type": "string"}}}}}],
        })
        assert response.status_code == 502
        assert response.json()["error"]["type"] == "tool_protocol_invalid"
        assert len(sent) == 2

    @pytest.mark.parametrize("tool_choice", [
        "required", {"type": "function", "function": {"name": "lookup"}},
    ])
    def test_repair_cannot_finish_as_text_when_tool_choice_requires_a_call(
        self, router, tool_choice,
    ):
        mod, client, write_state, _calls = router
        write_state()
        sent = []
        def handler(request):
            sent.append(json.loads(request.content))
            content = ("<tool_call>\n<function=missing_tool>\n"
                       "<parameter=key>\nx\n</parameter>\n</function>\n</tool_call>"
                       if len(sent) == 1 else "I did the work.")
            return httpx.Response(200, json={"model": "Concrete.gguf",
                "choices": [{"index": 0, "message": {"role": "assistant",
                    "content": content}, "finish_reason": "stop"}]})
        asyncio.run(mod.app.state.http.aclose())
        mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        response = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": False, "tool_choice": tool_choice,
            "messages": [{"role": "user", "content": "look this up"}],
            "tools": [{"type": "function", "function": {"name": "lookup",
                "parameters": {"type": "object", "properties": {}}}}],
        })
        assert len(sent) == 2
        assert response.status_code == 502
        assert response.json()["error"]["type"] == "tool_protocol_invalid"

    @pytest.mark.parametrize("call", [
        {"type": "function", "function": {"name": "lookup", "arguments": "{}"}},
        {"id": "call-1", "type": "not-function",
         "function": {"name": "lookup", "arguments": "{}"}},
    ])
    def test_nonstream_repair_rejects_malformed_structured_call(self, router, call):
        mod, client, write_state, _calls = router
        write_state()
        sent = []
        def handler(request):
            sent.append(json.loads(request.content))
            message = ({"role": "assistant", "content":
                "<tool_call>\n<function=missing_tool>\n"
                "<parameter=key>\nx\n</parameter>\n</function>\n</tool_call>"}
                if len(sent) == 1 else
                {"role": "assistant", "content": None, "tool_calls": [call]})
            return httpx.Response(200, json={"model": "Concrete.gguf",
                "choices": [{"index": 0, "message": message,
                             "finish_reason": "stop" if len(sent) == 1 else "tool_calls"}]})
        asyncio.run(mod.app.state.http.aclose())
        mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        response = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": False,
            "messages": [{"role": "user", "content": "look this up"}],
            "tools": [{"type": "function", "function": {"name": "lookup",
                "parameters": {"type": "object", "properties": {}}}}],
        })
        assert len(sent) == 2
        assert response.status_code == 502
        assert response.json()["error"]["type"] == "tool_protocol_invalid"

    @pytest.mark.parametrize("content,tool_choice,tools", [
        ("Some prose <tool_call>\n<function=missing_tool>\n</function>\n</tool_call>", "auto", True),
        ("<tool_call>\n<function=missing_tool>\n</function>", "auto", True),
        ("<tool_call>\n<function=missing_tool>\n</function>\n</tool_call>", "none", True),
        ("<tool_call>\n<function=missing_tool>\n</function>\n</tool_call>", "auto", False),
    ])
    def test_native_repair_requires_complete_exclusive_authorized_envelope(
        self, router, content, tool_choice, tools,
    ):
        mod, client, write_state, _calls = router
        write_state()
        sent = []
        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"model": "Concrete.gguf",
                "choices": [{"index": 0, "message": {"role": "assistant",
                    "content": content}, "finish_reason": "stop"}]})
        asyncio.run(mod.app.state.http.aclose())
        mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        body = {"model": "ods/current", "stream": True, "tool_choice": tool_choice,
            "messages": [{"role": "user", "content": "test"}]}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": "lookup",
                "parameters": {"type": "object", "properties": {}}}}]
        response = client.post("/v1/chat/completions", json=body)
        assert response.status_code == 200
        assert len(sent) == 1

    def test_repaired_structured_calls_obey_forced_name_and_parallel_limit(self, router):
        mod, _client, _write_state, _calls = router
        call = lambda name: {"id": "id-" + name, "type": "function",
            "function": {"name": name, "arguments": '{}'}}
        tools = [{"type": "function", "function": {"name": name,
            "parameters": {"type": "object", "properties": {}}}}
                 for name in ("lookup", "other")]
        completion = {"choices": [{"message": {"role": "assistant",
            "content": None, "tool_calls": [call("other")]},
            "finish_reason": "tool_calls"}]}
        request = {"tools": tools, "tool_choice": {"type": "function",
            "function": {"name": "lookup"}}, "parallel_tool_calls": False}
        assert mod._repaired_tool_decision_invalid(completion, request) is True
        completion["choices"][0]["message"]["tool_calls"] = [call("lookup"), call("other")]
        assert mod._repaired_tool_decision_invalid(completion, request) is True
        completion["choices"][0]["message"]["tool_calls"] = [call("lookup")]
        assert mod._repaired_tool_decision_invalid(completion, request) is False

    @pytest.mark.parametrize("role,reason,calls,invalid", [
        ("assistant", "stop", None, False),
        ("assistant", "length", None, True),
        ("assistant", "tool_calls", None, True),
        ("user", "stop", None, True),
        ("assistant", "stop", [{"id": "c1", "type": "function",
            "function": {"name": "lookup", "arguments": "{}"}}], True),
        ("assistant", "tool_calls", [{"id": "c1", "type": "function",
            "function": {"name": "lookup", "arguments": "{}"}}], False),
    ])
    def test_repaired_completion_requires_consistent_role_and_finish(
        self, router, role, reason, calls, invalid,
    ):
        mod, _client, _write_state, _calls = router
        message = {"role": role, "content": "Normal answer"}
        if calls is not None:
            message["tool_calls"] = calls
        completion = {"choices": [{"message": message,
                                  "finish_reason": reason}]}
        request = {"tools": [{"type": "function", "function": {
            "name": "lookup", "parameters": {"type": "object",
                "properties": {}}}}]}
        assert mod._repaired_tool_decision_invalid(completion, request) is invalid

    def test_refusal_native_markup_never_triggers_protocol_repair(self, router):
        mod, client, write_state, _calls = router
        write_state()
        sent = []
        native = ("<tool_call>\n<function=missing_tool>\n"
                  "</function>\n</tool_call>")
        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"model": "Concrete.gguf",
                "choices": [{"index": 0, "message": {
                    "role": "assistant", "content": native,
                    "refusal": "I cannot comply"},
                    "finish_reason": "stop"}]})
        asyncio.run(mod.app.state.http.aclose())
        mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        response = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True,
            "messages": [{"role": "user", "content": "test"}],
            "tools": [{"type": "function", "function": {"name": "lookup",
                "parameters": {"type": "object", "properties": {}}}}],
        })
        assert response.status_code == 200
        assert len(sent) == 1

    def test_local_tool_stream_preserves_backend_error_status(self, router):
        mod, client, write_state, _calls = router
        write_state()

        def handler(_request):
            return httpx.Response(503, json={"error": {"message": "backend busy"}})

        asyncio.run(mod.app.state.http.aclose())
        mod.app.state.http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler))
        response = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True, "messages": [],
            "tools": [{"type": "function", "function": {"name": "lookup"}}],
        })
        assert response.status_code == 503
        assert response.json()["error"]["message"] == "backend busy"
        assert "[DONE]" not in response.text

    def test_local_tool_stream_rejects_malformed_completed_call(self, router):
        mod, client, write_state, _calls = router
        write_state()

        def handler(_request):
            return httpx.Response(200, json={
                "model": "Concrete.gguf", "choices": [{
                    "index": 0, "message": {"role": "assistant",
                    "tool_calls": [{"id": "call-1", "type": "function",
                                    "function": {"name": "lookup",
                                                 "arguments": {"key": "ok"}}}]},
                    "finish_reason": "tool_calls",
                }],
            })

        asyncio.run(mod.app.state.http.aclose())
        mod.app.state.http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler))
        response = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True, "messages": [],
            "tools": [{"type": "function", "function": {"name": "lookup"}}],
        })
        assert response.status_code == 502
        assert response.json()["error"]["type"] == "upstream_invalid_response"
        assert "[DONE]" not in response.text

    def test_other_backend_keeps_native_tool_stream(self, router):
        mod, client, write_state, calls = router
        write_state(mutate=lambda state: state["active"]["backend"].update(
            kind="unknown"))
        response = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True, "messages": [],
            "tools": [{"type": "function", "function": {"name": "lookup"}}],
        })
        assert response.status_code == 200
        assert calls[-1]["stream"] is True

    def test_chat_template_artifacts_stripped_from_sse_delta(self, router):
        mod, client, write_state, calls = router
        write_state()
        _set_stream_upstream(mod, [
            b'data: {"id":"c1","model":"Concrete.gguf",'
            b'"choices":[{"delta":{"content":"O<|im_start|>assistant<|im_end|>DSVAL"}}]}\n\n',
            b"data: [DONE]\n\n",
        ])

        with client.stream("POST", "/v1/chat/completions", json={
            "model": "ods/current",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        }) as resp:
            raw = b"".join(resp.iter_bytes())

        assert resp.status_code == 200
        assert b'"content":"ODSVAL"' in raw
        assert b"<|im_start|>" not in raw
        assert b"Concrete.gguf" not in raw

    def test_chat_template_artifacts_stripped_from_model_less_sse_delta(self, router):
        mod, client, write_state, calls = router
        write_state()
        _set_stream_upstream(mod, [
            b'data: {"id":"c1","model":"Concrete.gguf","choices":[]}\n\n',
            b'data: {"id":"c1","choices":[{"delta":{"content":"A<|start_header_id|>assistant<|end_header_id|>B"}}]}\n\n',
            b"data: [DONE]\n\n",
        ])

        with client.stream("POST", "/v1/chat/completions", json={
            "model": "ods/current",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        }) as resp:
            raw = b"".join(resp.iter_bytes())

        assert resp.status_code == 200
        assert b'"content":"AB"' in raw
        assert b"<|start_header_id|>" not in raw
        assert b"Concrete.gguf" not in raw

    def test_client_authorization_stripped_and_backend_key_injected(self, router):
        mod, client, write_state, calls = router
        import os
        os.environ["KEYED_API_KEY"] = "backend-secret"
        write_state(endpoint="keyed")
        resp = client.post("/v1/chat/completions",
                           headers={"Authorization": "Bearer client-secret"},
                           json={"model": "ods/current", "messages": []})
        assert resp.status_code == 200
        assert calls[-1]["auth"] == "Bearer backend-secret"

    def test_unknown_path_rejected(self, router):
        mod, client, write_state, calls = router
        write_state()
        assert client.post("/v1/embeddings", json={}).status_code == 404
        assert client.get("/v1/chat/completions").status_code == 404
        assert calls == []

    def test_oversized_body_rejected(self, router):
        mod, client, write_state, calls = router
        write_state()
        monkey_big = "x" * (mod.MAX_BODY_BYTES + 10)
        resp = client.post("/v1/chat/completions",
                           content=monkey_big.encode(),
                           headers={"content-type": "application/json"})
        assert resp.status_code == 413

    def test_malformed_json_rejected(self, router):
        mod, client, write_state, calls = router
        write_state()
        resp = client.post("/v1/chat/completions", content=b"{nope",
                           headers={"content-type": "application/json"})
        assert resp.status_code == 400

    def test_no_route_yields_503(self, router):
        mod, client, write_state, calls = router
        resp = client.post("/v1/chat/completions",
                           json={"model": "ods/current", "messages": []})
        assert resp.status_code == 503
        assert resp.json()["error"]["type"] == "no_active_route"

    def test_unlisted_endpoint_yields_503(self, router):
        mod, client, write_state, calls = router
        write_state(endpoint="not-in-allowlist")
        resp = client.post("/v1/chat/completions",
                           json={"model": "ods/current", "messages": []})
        assert resp.status_code == 503
        assert resp.json()["error"]["type"] == "endpoint_not_allowlisted"

    def test_queue_mode_times_out_with_swap_code(self, router, monkeypatch):
        mod, client, write_state, calls = router
        write_state(queue=True)
        monkeypatch.setattr(mod, "QUEUE_WAIT_SECONDS", 0)
        resp = client.post("/v1/chat/completions",
                           json={"model": "ods/current", "messages": []})
        assert resp.status_code == 503
        assert resp.json()["error"]["type"] == "model_swap_in_progress"
        assert "Retry-After" in resp.headers

    def test_swap_gate_queues_new_request_until_owner_reopens(self, router):
        _mod, client, write_state, calls = router
        write_state()
        headers = {"Authorization": "Bearer internal-secret"}
        token = "0123456789abcdef0123456789abcdef"

        closed = client.post("/internal/model-swap/admission", headers=headers, json={
            "action": "begin", "token": token, "leaseSeconds": 30,
        })
        assert closed.status_code == 200
        assert closed.json()["status"] == "closed"

        result = {}

        def make_request():
            result["response"] = client.post("/v1/chat/completions", json={
                "model": "ods/current", "messages": [],
            })

        worker = threading.Thread(target=make_request)
        worker.start()
        for _ in range(100):
            health = client.get("/health").json()
            if health["queuedRequests"] == 1:
                break
            time.sleep(0.01)
        assert health["modelSwapGateActive"] is True
        assert health["activeRequests"] == 0
        assert health["queuedRequests"] == 1
        assert calls == []

        opened = client.post("/internal/model-swap/admission", headers=headers, json={
            "action": "end", "token": token,
        })
        assert opened.status_code == 200
        worker.join(5)
        assert not worker.is_alive()
        assert result["response"].status_code == 200
        assert len(calls) == 1

    def test_swap_gate_is_authenticated_and_single_owner(self, router):
        mod, client, write_state, calls = router
        first = "0123456789abcdef0123456789abcdef"
        second = "fedcba9876543210fedcba9876543210"
        payload = {"action": "begin", "token": first, "leaseSeconds": 30}
        assert client.post("/internal/model-swap/admission", json=payload).status_code == 401
        headers = {"Authorization": "Bearer internal-secret"}
        assert client.post(
            "/internal/model-swap/admission", headers=headers, json=payload
        ).status_code == 200
        conflict = client.post("/internal/model-swap/admission", headers=headers, json={
            "action": "begin", "token": second, "leaseSeconds": 30,
        })
        assert conflict.status_code == 409
        wrong_end = client.post("/internal/model-swap/admission", headers=headers, json={
            "action": "end", "token": second,
        })
        assert wrong_end.status_code == 409
        assert client.get("/health").json()["modelSwapGateActive"] is True

    def test_fragmented_sse_is_framed_before_rewrite(self, router):
        mod, client, write_state, calls = router
        write_state()
        _set_stream_upstream(mod, [
            b'data: {"id":"c1","mod',
            b'el":"Concrete.gguf","choices":[{"delta":{"content":"hi"}}]}\r',
            b'\n\r\ndata: [DONE]\r\n\r\n',
        ])
        with client.stream("POST", "/v1/chat/completions", json={
            "model": "default", "stream": True, "messages": [],
        }) as resp:
            raw = b"".join(resp.iter_bytes())
        assert resp.status_code == 200
        assert b'"model":"default"' in raw
        assert b"Concrete.gguf" not in raw
        assert raw.endswith(b"data: [DONE]\r\n\r\n")

    def test_stream_holds_admission_until_teardown(self, router, monkeypatch):
        mod, client, write_state, calls = router
        write_state()
        started = threading.Event()
        release = threading.Event()
        monkeypatch.setattr(mod, "MAX_QUEUE_DEPTH", 1)
        _set_stream_upstream(
            mod,
            [b'data: {"model":"Concrete.gguf"}\n\n', b"data: [DONE]\n\n"],
            started=started,
            release=release,
        )
        result = {}

        def consume_stream():
            result["response"] = client.post("/v1/chat/completions", json={
                "model": "ods/current", "stream": True, "messages": [],
            })

        worker = threading.Thread(target=consume_stream)
        worker.start()
        assert started.wait(5)
        assert mod._inflight == 1
        health = client.get("/health").json()
        assert health["activeRequests"] == 1
        assert health["queuedRequests"] == 0
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        assert result["response"].status_code == 200
        assert mod._inflight == 0


class TestStateTrust:
    def test_schema_invalid_state_is_not_routable(self, router):
        mod, client, write_state, calls = router
        write_state(mutate=lambda doc: doc.update({"unexpected": True}))
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "messages": [],
        })
        assert resp.status_code == 503
        assert calls == []

    @pytest.mark.parametrize("mutation", [
        lambda doc: doc["active"].update({"reconstructed": True}),
        lambda doc: doc["active"]["proof"].update({"completion": False}),
        lambda doc: doc["active"]["proof"].update({"identity": "Other.gguf"}),
        lambda doc: doc["active"].update({"verifiedAt": None}),
        lambda doc: doc.update({"routeSeq": doc["routeSeq"] + 1}),
    ])
    def test_unverified_state_is_not_routable(self, router, mutation):
        mod, client, write_state, calls = router
        write_state(mutate=mutation)
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "messages": [],
        })
        assert resp.status_code == 503
        assert calls == []

    def test_regressed_state_retains_verified_last_known_good(self, router):
        mod, client, write_state, calls = router
        write_state(runtime="New.gguf", route_seq=9)
        assert client.get("/v1/models").json()["ods"]["routedModel"] == "New.gguf"
        write_state(runtime="Old.gguf", route_seq=8)
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "messages": [],
        })
        assert resp.status_code == 200
        assert calls[-1]["model"] == "New.gguf"

    def test_invalid_update_retains_verified_last_known_good(self, router):
        mod, client, write_state, calls = router
        write_state(runtime="Good.gguf", route_seq=9)
        assert client.get("/v1/models").status_code == 200
        write_state(
            runtime="Unproved.gguf", route_seq=10,
            mutate=lambda doc: doc["active"]["proof"].update({"completion": False}),
        )
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "messages": [],
        })
        assert resp.status_code == 200
        assert calls[-1]["model"] == "Good.gguf"

    def test_same_sequence_cannot_mutate_route(self, router):
        mod, client, write_state, calls = router
        write_state(runtime="Original.gguf", route_seq=9)
        assert client.get("/v1/models").status_code == 200
        write_state(runtime="Replacement.gguf", route_seq=9)
        client.post("/v1/chat/completions", json={
            "model": "ods/current", "messages": [],
        })
        assert calls[-1]["model"] == "Original.gguf"


class TestIngressLimits:
    def test_chunked_body_stops_at_limit(self, router, monkeypatch):
        mod, client, write_state, calls = router
        monkeypatch.setattr(mod, "MAX_BODY_BYTES", 5)
        messages = iter([
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ])

        async def receive():
            return next(messages)

        request = Request({"type": "http", "method": "POST", "path": "/",
                           "headers": []}, receive)
        with pytest.raises(mod.RouterError) as exc:
            asyncio.run(mod._read_bounded_body(request))
        assert exc.value.status == 413


class TestModelsAndEvidence:
    def test_models_lists_aliases_with_ods_metadata(self, router):
        mod, client, write_state, calls = router
        write_state()
        body = client.get("/v1/models").json()
        ids = [m["id"] for m in body["data"]]
        assert ids == ["ods/current", "default"]
        assert body["ods"]["routedModel"] == "Concrete.gguf"

    def test_probe_marker_records_evidence(self, router):
        mod, client, write_state, calls = router
        write_state()
        probe_id = str(uuid.uuid4())
        marker = _signed_marker(probe_id)
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": f"hello {marker}"}],
        })
        assert resp.status_code == 200
        ev = client.get(f"/internal/route-evidence/{probe_id}",
                        headers={"Authorization": "Bearer internal-secret"})
        assert ev.status_code == 200
        record = ev.json()
        assert record["requestedModel"] == "ods/current"
        assert record["routedModel"] == "Concrete.gguf"
        assert record["routeSeq"] == 7
        assert record["responseModel"] == "Concrete.gguf"
        assert "messages" not in record and "content" not in record

    def test_completed_verified_stream_records_evidence(self, router):
        mod, client, write_state, calls = router
        write_state()
        probe_id = str(uuid.uuid4())
        marker = _signed_marker(probe_id)
        _set_stream_upstream(mod, [
            b'data: {"id":"c1","model":"Con',
            b'crete.gguf","choices":[{"delta":{"content":"ok"}}]}\n\n',
            b"data: [DONE]\n\n",
        ])
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True,
            "messages": [{"role": "user", "content": marker}],
        })
        assert resp.status_code == 200
        ev = client.get(
            f"/internal/route-evidence/{probe_id}",
            headers={"Authorization": "Bearer internal-secret"},
        )
        assert ev.status_code == 200
        assert ev.json()["responseModel"] == "Concrete.gguf"

    def test_unpinned_stream_records_observed_backend_identity(
            self, router):
        mod, client, write_state, calls = router
        write_state()
        probe_id = str(uuid.uuid4())
        _set_stream_upstream(
            mod,
            [b'data: {"model":"Wrong.gguf","choices":[]}\n\n'
             b'data: [DONE]\n\n'],
        )
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True,
            "messages": [{"role": "user", "content": _signed_marker(probe_id)}],
        })
        assert resp.status_code == 200
        ev = client.get(
            f"/internal/route-evidence/{probe_id}",
            headers={"Authorization": "Bearer internal-secret"},
        )
        assert ev.status_code == 200
        assert ev.json()["routedModel"] == "Concrete.gguf"
        assert ev.json()["responseModel"] == "Wrong.gguf"

    def test_stream_with_inconsistent_backend_identity_records_no_evidence(
            self, router):
        mod, client, write_state, calls = router
        write_state()
        probe_id = str(uuid.uuid4())
        _set_stream_upstream(mod, [
            b'data: {"model":"Concrete.gguf","choices":[]}\n\n',
            b'data: {"model":"Wrong.gguf","choices":[]}\n\n',
            b'data: [DONE]\n\n',
        ])
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True,
            "messages": [{"role": "user", "content": _signed_marker(probe_id)}],
        })
        assert resp.status_code == 200
        ev = client.get(
            f"/internal/route-evidence/{probe_id}",
            headers={"Authorization": "Bearer internal-secret"},
        )
        assert ev.status_code == 404

    def test_completed_stream_without_identity_records_unknown_response_model(
            self, router):
        mod, client, write_state, calls = router
        write_state()
        probe_id = str(uuid.uuid4())
        _set_stream_upstream(
            mod,
            [b'data: {"choices":[{"delta":{"content":"no identity"}}]}\n\n'
             b'data: [DONE]\n\n'],
        )
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True,
            "messages": [{"role": "user", "content": _signed_marker(probe_id)}],
        })
        assert resp.status_code == 200
        ev = client.get(
            f"/internal/route-evidence/{probe_id}",
            headers={"Authorization": "Bearer internal-secret"},
        )
        assert ev.status_code == 200
        record = ev.json()
        assert record["requestedModel"] == "ods/current"
        assert record["routedModel"] == "Concrete.gguf"
        assert record["responseModel"] == ""

    def test_failed_stream_records_no_evidence_and_releases_admission(self, router):
        mod, client, write_state, calls = router
        write_state()
        probe_id = str(uuid.uuid4())
        _set_stream_upstream(
            mod,
            [b'data: {"model":"Concrete.gguf"}\n\n'],
            model_error=httpx.ReadError("truncated stream"),
        )
        with pytest.raises(httpx.ReadError, match="truncated stream"):
            client.post("/v1/chat/completions", json={
                "model": "ods/current", "stream": True,
                "messages": [
                    {"role": "user", "content": _signed_marker(probe_id)}
                ],
            })
        ev = client.get(
            f"/internal/route-evidence/{probe_id}",
            headers={"Authorization": "Bearer internal-secret"},
        )
        assert ev.status_code == 404
        assert mod._inflight == 0

    def test_forged_marker_records_nothing(self, router):
        mod, client, write_state, calls = router
        write_state()
        probe_id = str(uuid.uuid4())
        marker = f"[ODS_PROBE id={probe_id} sig=Zm9yZ2Vk]"
        client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": marker}],
        })
        ev = client.get(f"/internal/route-evidence/{probe_id}",
                        headers={"Authorization": "Bearer internal-secret"})
        assert ev.status_code == 404

    def test_evidence_requires_bearer(self, router):
        mod, client, write_state, calls = router
        assert client.get("/internal/route-evidence/x").status_code == 401
        wrong = client.get("/internal/route-evidence/x",
                           headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401

    def test_health_reports_route_presence(self, router):
        mod, client, write_state, calls = router
        assert client.get("/health").json()["hasRoute"] is False
        write_state()
        assert client.get("/health").json()["hasRoute"] is True


class TestProbeKeyLifecycleAndInstance:
    """File-based probe key: rotate/remove without recreating the router,
    with the env value as fallback; instance identity is stable and stamped
    into evidence so a mid-cycle replacement is detectable."""

    def _use_key_file(self, mod, tmp_path, monkeypatch, env_fallback=""):
        key_path = tmp_path / "fleet-probe.key"
        monkeypatch.setattr(mod, "PROBE_KEY_PATH", key_path)
        monkeypatch.setattr(mod, "PROBE_KEY", env_fallback)
        mod._probe_key_cache.update({"mtime": None, "key": ""})
        return key_path

    def test_file_key_rotates_without_restart(self, router, tmp_path, monkeypatch):
        mod, client, write_state, calls = router
        write_state()
        key_path = self._use_key_file(mod, tmp_path, monkeypatch)
        key_path.write_text("key-a\n", encoding="utf-8")

        instance_before = client.get("/health").json()["instanceId"]
        probe_a = str(uuid.uuid4())
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user",
                          "content": _signed_marker(probe_a, key="key-a")}],
        })
        assert resp.status_code == 200
        ev = client.get(f"/internal/route-evidence/{probe_a}",
                        headers={"Authorization": "Bearer internal-secret"})
        assert ev.status_code == 200
        assert ev.json()["instanceId"] == instance_before

        key_path.write_text("key-b-rotated\n", encoding="utf-8")
        stale = str(uuid.uuid4())
        client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user",
                          "content": _signed_marker(stale, key="key-a")}],
        })
        assert client.get(
            f"/internal/route-evidence/{stale}",
            headers={"Authorization": "Bearer internal-secret"},
        ).status_code == 404

        fresh = str(uuid.uuid4())
        client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user",
                          "content": _signed_marker(fresh, key="key-b-rotated")}],
        })
        assert client.get(
            f"/internal/route-evidence/{fresh}",
            headers={"Authorization": "Bearer internal-secret"},
        ).status_code == 200

        health = client.get("/health").json()
        assert health["instanceId"] == instance_before
        assert health["probeKeyConfigured"] is True

    def test_deleted_key_file_stops_collection_then_env_fallback(
            self, router, tmp_path, monkeypatch):
        mod, client, write_state, calls = router
        write_state()
        key_path = self._use_key_file(mod, tmp_path, monkeypatch)
        key_path.write_text("key-a", encoding="utf-8")
        assert client.get("/health").json()["probeKeyConfigured"] is True

        key_path.unlink()
        assert client.get("/health").json()["probeKeyConfigured"] is False
        orphan = str(uuid.uuid4())
        client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user",
                          "content": _signed_marker(orphan, key="key-a")}],
        })
        assert client.get(
            f"/internal/route-evidence/{orphan}",
            headers={"Authorization": "Bearer internal-secret"},
        ).status_code == 404

        monkeypatch.setattr(mod, "PROBE_KEY", "env-fallback-key")
        assert client.get("/health").json()["probeKeyConfigured"] is True
        fallback = str(uuid.uuid4())
        client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user",
                          "content": _signed_marker(fallback,
                                                    key="env-fallback-key")}],
        })
        assert client.get(
            f"/internal/route-evidence/{fallback}",
            headers={"Authorization": "Bearer internal-secret"},
        ).status_code == 200

    def test_empty_key_file_falls_back_to_env(self, router, tmp_path, monkeypatch):
        mod, client, write_state, calls = router
        write_state()
        key_path = self._use_key_file(mod, tmp_path, monkeypatch,
                                      env_fallback="env-key")
        key_path.write_text("   \n", encoding="utf-8")
        assert client.get("/health").json()["probeKeyConfigured"] is True
        probe = str(uuid.uuid4())
        client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user",
                          "content": _signed_marker(probe, key="env-key")}],
        })
        assert client.get(
            f"/internal/route-evidence/{probe}",
            headers={"Authorization": "Bearer internal-secret"},
        ).status_code == 200

    def test_health_exposes_stable_instance_and_key_state(self, router):
        mod, client, write_state, calls = router
        first = client.get("/health").json()
        second = client.get("/health").json()
        assert first["instanceId"] == second["instanceId"] == mod.INSTANCE_ID
        assert uuid.UUID(first["instanceId"])
        assert first["probeKeyConfigured"] is True  # fixture env key
        assert first["activeRequests"] == 0
        assert first["queuedRequests"] == 0
        assert first["modelSwapGateActive"] is False


class TestEndpointsReload:
    """endpoints.json is re-read on mtime/size change (activation re-renders
    it); missing or invalid content retains the last good allowlist."""

    def _endpoints_path(self, tmp_path):
        return tmp_path / "endpoints.json"

    def test_new_endpoint_visible_without_restart(self, router, tmp_path):
        mod, client, write_state, calls = router
        write_state(endpoint="added-later")
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 503
        assert resp.json()["error"]["type"] == "endpoint_not_allowlisted"

        self._endpoints_path(tmp_path).write_text(json.dumps({
            "endpoints": [
                {"id": "llama-server-default", "baseUrl": "http://upstream:8080"},
                {"id": "added-later", "baseUrl": "http://late:7000"},
            ]
        }), encoding="utf-8")
        ok = client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert ok.status_code == 200
        assert calls[-1]["url"].startswith("http://late:7000")

    def test_invalid_rewrite_retains_last_good_allowlist(self, router, tmp_path):
        mod, client, write_state, calls = router
        write_state()
        assert client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": "hi"}],
        }).status_code == 200

        self._endpoints_path(tmp_path).write_text("{not-json", encoding="utf-8")
        still_ok = client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert still_ok.status_code == 200
        assert calls[-1]["url"].startswith("http://upstream:8080")

    def test_health_reports_allowlist_health(self, router, tmp_path):
        mod, client, write_state, calls = router
        healthy = client.get("/health")
        assert healthy.status_code == 200
        assert healthy.json()["status"] == "ok"
        assert healthy.json()["endpointCount"] == 2

        self._endpoints_path(tmp_path).write_text(
            json.dumps({"endpoints": []}), encoding="utf-8")
        degraded = client.get("/health")
        # HTTP 200 on purpose: the compose healthcheck must not turn a
        # config gap into a restart cascade; the body carries the signal.
        assert degraded.status_code == 200
        assert degraded.json()["status"] == "degraded"
        assert degraded.json()["endpointCount"] == 0


class TestRoutedTelemetry:
    def test_router_event_bounds_upstream_usage_to_ingest_contract(self, router):
        mod, client, write_state, calls = router
        event = mod._build_telemetry_event(
            {"messages": [{}] * 100_001},
            raw_body_bytes=mod.MAX_BODY_BYTES + 1,
            model="Concrete.gguf",
            backend="llama-server",
            path="/v1/chat/completions",
            duration_ms=100_000_000,
            usage={
                "input_tokens": 3_000_000_000,
                "output_tokens": -1,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            },
            stop_reason="stop",
        )

        assert event["request_body_bytes"] == mod.MAX_BODY_BYTES
        assert event["message_count"] == 100_000
        assert event["input_tokens"] == 2_000_000_000
        assert event["output_tokens"] == 0
        assert event["duration_ms"] == 86_400_000

    def test_nonstream_usage_is_emitted_without_message_content(self, router):
        mod, client, write_state, calls = router
        write_state()
        recorder = _RecordingTelemetry()
        mod.app.state.telemetry = recorder

        def handler(_request):
            return httpx.Response(200, json={
                "id": "c1",
                "model": "Concrete.gguf",
                "choices": [{
                    "message": {"role": "assistant", "content": "secret answer"},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 5,
                    "prompt_tokens_details": {"cached_tokens": 3},
                },
            })

        asyncio.run(mod.app.state.http.aclose())
        mod.app.state.http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        response = client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [
                {"role": "user", "content": "secret prompt"},
                {"role": "assistant", "content": "prior secret"},
            ],
            "tools": [{"type": "function", "function": {"name": "private"}}],
        })

        assert response.status_code == 200
        assert len(recorder.events) == 1
        event = recorder.events[0]
        assert event["model"] == "Concrete.gguf"
        assert event["provider_name"] == "llama-server"
        assert event["message_count"] == 2
        assert event["user_message_count"] == 1
        assert event["assistant_message_count"] == 1
        assert event["tool_count"] == 1
        assert event["input_tokens"] == 17
        assert event["output_tokens"] == 5
        assert event["cache_read_tokens"] == 3
        assert event["stop_reason"] == "stop"
        serialized = json.dumps(event)
        assert "secret prompt" not in serialized
        assert "secret answer" not in serialized
        assert "prior secret" not in serialized
        assert "private" not in serialized

    def test_stream_usage_is_emitted_only_after_complete_stream(self, router):
        mod, client, write_state, calls = router
        write_state()
        recorder = _RecordingTelemetry()
        mod.app.state.telemetry = recorder
        _set_stream_upstream(mod, [
            (
                b'data: {"model":"Concrete.gguf","choices":'
                b'[{"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
            ),
            (
                b'data: {"model":"Concrete.gguf","choices":'
                b'[{"delta":{},"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":11,"completion_tokens":7}}\n\n'
                b"data: [DONE]\n\n"
            ),
        ])

        with client.stream("POST", "/v1/chat/completions", json={
            "model": "default",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        }) as response:
            assert response.status_code == 200
            b"".join(response.iter_bytes())

        assert len(recorder.events) == 1
        assert recorder.events[0]["input_tokens"] == 11
        assert recorder.events[0]["output_tokens"] == 7
        assert recorder.events[0]["stop_reason"] == "stop"

    def test_truncated_stream_without_terminal_event_is_not_emitted(self, router):
        mod, client, write_state, calls = router
        write_state()
        recorder = _RecordingTelemetry()
        mod.app.state.telemetry = recorder
        _set_stream_upstream(mod, [
            (
                b'data: {"model":"Concrete.gguf","choices":'
                b'[{"delta":{"content":"partial"},"finish_reason":null}],'
                b'"usage":{"prompt_tokens":11,"completion_tokens":1}}\n\n'
            ),
        ])

        with client.stream("POST", "/v1/chat/completions", json={
            "model": "default",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        }) as response:
            assert response.status_code == 200
            raw = b"".join(response.iter_bytes())

        assert b"partial" in raw
        assert recorder.events == []

    def test_responses_completed_event_emits_stream_usage(self, router):
        mod, client, write_state, calls = router
        write_state()
        recorder = _RecordingTelemetry()
        mod.app.state.telemetry = recorder
        _set_stream_upstream(mod, [
            (
                b'data: {"type":"response.output_text.delta",'
                b'"response":{"model":"Concrete.gguf"},'
                b'"delta":"hi"}\n\n'
            ),
            (
                b'data: {"type":"response.completed","response":'
                b'{"model":"Concrete.gguf","status":"completed",'
                b'"usage":{"input_tokens":9,"output_tokens":4}}}\n\n'
            ),
        ])

        with client.stream("POST", "/v1/responses", json={
            "model": "default",
            "stream": True,
            "input": "hi",
        }) as response:
            assert response.status_code == 200
            b"".join(response.iter_bytes())

        assert len(recorder.events) == 1
        assert recorder.events[0]["input_tokens"] == 9
        assert recorder.events[0]["output_tokens"] == 4
        assert recorder.events[0]["stop_reason"] == "completed"

    def test_telemetry_failure_never_changes_model_response(self, router):
        mod, client, write_state, calls = router
        write_state()
        mod.app.state.telemetry = _RecordingTelemetry(raises=True)

        response = client.post("/v1/chat/completions", json={
            "model": "ods/current",
            "messages": [{"role": "user", "content": "hi"}],
        })

        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "hello"

    def test_sink_posts_authenticated_event_and_drains(self, monkeypatch):
        import app.main as mod

        received = []

        def handler(request):
            received.append({
                "url": str(request.url),
                "authorization": request.headers.get("authorization"),
                "body": json.loads(request.content),
            })
            return httpx.Response(202, json={"status": "accepted"})

        async def scenario():
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            )
            sink = mod._TelemetrySink(
                "http://token-spy:8080",
                "shared-secret",
                client=client,
            )
            await sink.start()
            assert sink.emit({"model": "Concrete.gguf"}) is True
            await asyncio.wait_for(sink.queue.join(), timeout=1)
            await sink.stop()

        asyncio.run(scenario())

        assert received == [{
            "url": "http://token-spy:8080/api/ingest/routed",
            "authorization": "Bearer shared-secret",
            "body": {"model": "Concrete.gguf"},
        }]


@pytest.mark.parametrize("responses", [False, True], ids=["chat", "responses"])
@pytest.mark.parametrize("cached,written,expected", [
    (0, 0, (20, 0, 0)),
    (8, 0, (12, 8, 0)),
    (20, 0, (0, 20, 0)),
    (8, 4, (8, 8, 4)),
    (30, 4, (0, 20, 0)),
])
def test_routed_cache_categories_partition_prompt_total(router, responses, cached, written, expected):
    mod, client, write_state, _ = router
    write_state()
    recorder = _RecordingTelemetry()
    mod.app.state.telemetry = recorder
    usage = ({
        "input_tokens": 20, "output_tokens": 5,
        "input_tokens_details": {"cached_tokens": cached},
    } if responses else {
        "prompt_tokens": 20, "completion_tokens": 5,
        "prompt_tokens_details": {"cached_tokens": cached},
    })
    usage["cache_write_tokens"] = written
    upstream = {"model": "Concrete.gguf", "usage": usage}
    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=upstream))
    )
    path = "/v1/responses" if responses else "/v1/chat/completions"
    response = client.post(path, json={"model": "default", "messages": []})
    assert response.status_code == 200
    # Client-visible provider usage retains its original inclusive contract.
    assert response.json()["usage"] == usage
    assert len(recorder.events) == 1
    event = recorder.events[0]
    categories = tuple(event[key] for key in (
        "input_tokens", "cache_read_tokens", "cache_write_tokens"
    ))
    assert categories == expected
    assert sum(categories) + event["output_tokens"] == 25


@pytest.mark.parametrize("late_cache", [False, True])
def test_stream_cache_partition_uses_final_aggregate(router, late_cache):
    mod, client, write_state, _ = router
    write_state()
    recorder = _RecordingTelemetry()
    mod.app.state.telemetry = recorder
    snapshots = [
        {"prompt_tokens": 20, "completion_tokens": 1},
        {"prompt_tokens": 20, "completion_tokens": 5,
         "prompt_tokens_details": {"cached_tokens": 8}, "cache_write_tokens": 4},
    ]
    if not late_cache:
        snapshots.reverse()
    chunks = [
        ("data: " + json.dumps({"model": "Concrete.gguf", "usage": usage}) + "\n\n").encode()
        for usage in snapshots
    ]
    _set_stream_upstream(mod, chunks + [b"data: [DONE]\n\n"])
    response = client.post("/v1/chat/completions", json={
        "model": "default", "messages": [], "stream": True,
    })
    assert response.status_code == 200
    assert len(recorder.events) == 1
    event = recorder.events[0]
    assert [event[key] for key in (
        "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"
    )] == [8, 5, 8, 4]
