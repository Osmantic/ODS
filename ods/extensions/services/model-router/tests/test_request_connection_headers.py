"""Hop-specific request fields must not become end-to-end backend headers."""

import httpx
import pytest

from test_router import router as router


@pytest.mark.parametrize("connection", [
    [("Connection", "X-Hop-Only, keep-alive")],
    [("Connection", "x-HoP-OnLy"), ("Connection", "X-Other-Hop")],
])
def test_connection_nominated_fields_are_not_forwarded(router, connection):
    mod, client, write_state, _ = router
    write_state()
    seen = []

    def backend(request):
        seen.append(request.headers)
        return httpx.Response(200, json={"model": "Concrete.gguf", "choices": []})

    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    response = client.post("/v1/chat/completions", json={"model": "ods/current", "messages": []},
                           headers=connection + [
                               ("X-Hop-Only", "connection-scoped"),
                               ("X-Other-Hop", "second-connection-field"),
                               ("X-End-To-End", "retain-me"),
                           ])
    assert response.status_code == 200
    assert "x-hop-only" not in seen[0]
    if len(connection) > 1:
        assert "x-other-hop" not in seen[0]
    else:
        assert seen[0]["x-other-hop"] == "second-connection-field"
    assert seen[0]["x-end-to-end"] == "retain-me"
    assert seen[0]["content-type"] == "application/json"
    assert mod._inflight == 0


def test_backend_owned_credentials_are_applied_after_request_sanitization(router, monkeypatch):
    mod, client, write_state, _ = router
    write_state(endpoint="keyed")
    monkeypatch.setenv("KEYED_API_KEY", "backend-test-key")
    seen = []

    def backend(request):
        seen.append(request.headers)
        return httpx.Response(200, json={"model": "Concrete.gguf", "choices": []})

    mod.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    response = client.post("/v1/chat/completions", json={"model": "ods/current", "messages": []},
                           headers={
                               "Connection": "Authorization, X-Hop-Only, Content-Type",
                               "Authorization": "Bearer client-test-key",
                               "X-Hop-Only": "remove-me",
                           })
    assert response.status_code == 200
    assert seen[0]["authorization"] == "Bearer backend-test-key"
    assert "x-hop-only" not in seen[0]
    assert seen[0]["content-type"] == "application/json"
