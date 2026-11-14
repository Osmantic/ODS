"""Keep connection establishment separate from generation and repair budgets."""
import json
import httpx
import pytest
import test_router

router = test_router.router
_use_upstream = test_router._use_upstream


class TestUpstreamConnectTimeout:
    """Connect establishment is bounded separately from read/write/pool."""

    @pytest.mark.parametrize("budget", [600.0, 2.0])
    def test_nonstream_forward_passes_bounded_connect_timeout(self, router, monkeypatch, budget):
        mod, client, write_state, _calls = router
        write_state()
        monkeypatch.setattr(mod, "UPSTREAM_TIMEOUT_SECONDS", budget)
        seen = []

        def handler(request):
            seen.append(request.extensions.get("timeout"))
            return httpx.Response(200, json={
                "id": "c1", "model": "Concrete.gguf",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            })

        _use_upstream(mod, handler)
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200
        assert len(seen) == 1
        timeout = seen[0]
        assert timeout is not None
        assert timeout["connect"] == min(5.0, budget)
        assert timeout["read"] == budget
        assert timeout["write"] == budget
        assert timeout["pool"] == budget

    def test_streaming_forward_passes_bounded_connect_timeout(self, router, monkeypatch):
        mod, client, write_state, _calls = router
        write_state()
        monkeypatch.setattr(mod, "UPSTREAM_TIMEOUT_SECONDS", 600.0)
        seen = []

        def handler(request):
            seen.append(request.extensions.get("timeout"))
            return httpx.Response(
                200,
                content=b'data: {"model":"Concrete.gguf","choices":[]}\n\ndata: [DONE]\n\n',
                headers={"content-type": "text/event-stream"},
            )

        _use_upstream(mod, handler)
        with client.stream("POST", "/v1/chat/completions", json={
            "model": "ods/current", "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        }) as resp:
            assert resp.status_code == 200
            b"".join(resp.iter_bytes())
        assert len(seen) == 1
        assert seen[0]["connect"] == 5.0
        assert seen[0]["read"] == 600.0

    def test_thinking_compat_forward_passes_bounded_connect_timeout(self, router, monkeypatch):
        mod, client, write_state, _calls = router
        write_state()
        monkeypatch.setattr(mod, "UPSTREAM_TIMEOUT_SECONDS", 600.0)
        seen = []

        def handler(request):
            seen.append(request.extensions.get("timeout"))
            return httpx.Response(200, json={"model": "Concrete.gguf",
                "choices": [{"index": 0, "message": {"role": "assistant",
                    "content": None, "tool_calls": [{"id": "call-1",
                        "type": "function", "function": {"name": "lookup",
                        "arguments": '{"key":"ok"}'}}]},
                    "finish_reason": "tool_calls"}]})

        _use_upstream(mod, handler)
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True,
            "chat_template_kwargs": {"enable_thinking": True},
            "messages": [{"role": "user", "content": "look up ok"}],
            "tools": [{"type": "function", "function": {"name": "lookup",
                "parameters": {"type": "object", "properties": {"key": {"type": "string"}}}}}],
        })
        assert resp.status_code == 200
        assert len(seen) == 1
        assert seen[0]["connect"] == 5.0
        assert seen[0]["read"] == 600.0

    def test_completed_tool_stream_fallback_passes_bounded_connect_timeout(
            self, router, monkeypatch):
        mod, client, write_state, _calls = router
        write_state()
        monkeypatch.setattr(mod, "UPSTREAM_TIMEOUT_SECONDS", 600.0)
        seen = []

        def handler(request):
            body = json.loads(request.content)
            seen.append((body.get("stream"), request.extensions.get("timeout")))
            if body["stream"]:
                return httpx.Response(500, json={"error": {
                    "message": "Invalid diff: now finding less tool calls!"}})
            return httpx.Response(200, json={
                "id": "backend-call", "model": "Concrete.gguf",
                "choices": [{"index": 0, "message": {"role": "assistant",
                    "content": None, "tool_calls": [{"id": "call-1",
                        "type": "function", "function": {"name": "lookup",
                        "arguments": '{"key":"ok"}'}}]},
                    "finish_reason": "tool_calls"}],
            })

        _use_upstream(mod, handler)
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": True,
            "messages": [{"role": "user", "content": "look up ok"}],
            "tools": [{"type": "function", "function": {"name": "lookup",
                "parameters": {"type": "object"}}}],
        })
        assert resp.status_code == 200
        assert len(seen) == 2
        assert seen[0][0] is True and seen[0][1]["connect"] == 5.0
        assert seen[1][0] is False and seen[1][1]["connect"] == 5.0
        assert 0 < seen[1][1]["read"] < seen[0][1]["read"]
        assert seen[1][1]["read"] == seen[1][1]["write"] == seen[1][1]["pool"]

    def test_repair_forward_passes_bounded_connect_timeout(self, router, monkeypatch):
        mod, client, write_state, _calls = router
        write_state()
        monkeypatch.setattr(mod, "UPSTREAM_TIMEOUT_SECONDS", 600.0)
        seen = []
        native = ("<tool_call>\n<function=missing_tool>\n"
                  "<parameter=key>\nx\n</parameter>\n</function>\n</tool_call>")

        def handler(request):
            body = json.loads(request.content)
            seen.append((body.get("stream"), request.extensions.get("timeout")))
            if len(seen) == 1:
                return httpx.Response(200, json={"model": "Concrete.gguf",
                    "choices": [{"index": 0, "message": {"role": "assistant",
                        "content": native}, "finish_reason": "stop"}]})
            return httpx.Response(200, json={"model": "Concrete.gguf",
                "choices": [{"index": 0, "message": {"role": "assistant",
                    "content": None, "tool_calls": [{"id": "call-1",
                        "type": "function", "function": {"name": "lookup",
                        "arguments": '{"key":"ok"}'}}]},
                    "finish_reason": "tool_calls"}]})

        _use_upstream(mod, handler)
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "stream": False,
            "messages": [{"role": "user", "content": "look up ok"}],
            "tools": [{"type": "function", "function": {"name": "lookup",
                "parameters": {"type": "object", "properties": {"key": {"type": "string"}}}}}],
        })
        assert resp.status_code == 200
        assert len(seen) == 2
        assert seen[0][0] is False and seen[0][1]["connect"] == 5.0
        assert seen[1][0] is False and seen[1][1]["connect"] == 5.0
        assert 0 < seen[1][1]["read"] < seen[0][1]["read"]
        assert seen[1][1]["read"] == seen[1][1]["write"] == seen[1][1]["pool"]

    @pytest.mark.parametrize("failure,status", [(httpx.ConnectError, 502), (httpx.ConnectTimeout, 502), (httpx.ReadTimeout, 504)])
    def test_connect_failure_does_not_retry(self, router, monkeypatch, failure, status):
        mod, client, write_state, _calls = router
        write_state()
        monkeypatch.setattr(mod, "UPSTREAM_TIMEOUT_SECONDS", 600.0)
        sent = []

        def handler(request):
            sent.append(request)
            raise failure("connection failed", request=request)

        _use_upstream(mod, handler)
        resp = client.post("/v1/chat/completions", json={
            "model": "ods/current", "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == status
        assert "connection failed" not in resp.text
        assert len(sent) == 1, "router must not retry the POST"

    @pytest.mark.parametrize("failure,status", [(httpx.ConnectTimeout, 502), (httpx.ReadTimeout, 504)])
    def test_repair_distinguishes_connect_and_read_timeout(self, router, failure, status):
        mod, client, write_state, _calls = router
        write_state()
        sent = []

        def handler(request):
            sent.append(request)
            if len(sent) > 1:
                raise failure("private failure detail", request=request)
            return httpx.Response(200, json={"model": "Concrete.gguf", "choices": [{
                "index": 0, "message": {"role": "assistant", "content":
                    '<tool_call>\n<function=missing_tool>\n<parameter=key>\nx\n</parameter>\n</function>\n</tool_call>'},
                "finish_reason": "stop"}]})

        _use_upstream(mod, handler)
        response = client.post('/v1/chat/completions', json={
            'model': 'ods/current', 'messages': [{'role': 'user', 'content': 'look up x'}],
            'tools': [{'type': 'function', 'function': {'name': 'lookup', 'parameters': {'type': 'object'}}}]})
        assert response.status_code == status
        assert len(sent) == 2
        assert 'private failure detail' not in response.text
