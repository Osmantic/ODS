"""The concurrent router fixture must publish complete state like Switchboard."""

from pathlib import Path

from test_router import router  # noqa: F401


def test_requests_keep_the_previous_route_while_fixture_writes_replacement(router, monkeypatch):
    mod, client, write_state, calls = router
    write_state()
    responses = []
    original_write = Path.write_text

    def observe_during_write(path, data, **kwargs):
        if path.parent != mod.STATE_PATH.parent:
            return original_write(path, data, **kwargs)
        # Deterministically exercise the truncate/write window seen in CI.
        with path.open("w", **kwargs) as handle:
            responses.append(client.post("/v1/chat/completions", json={
                "model": "ods/current", "messages": [{"role": "user", "content": "synthetic"}],
            }))
            return handle.write(data)

    monkeypatch.setattr(Path, "write_text", observe_during_write)
    write_state(runtime="Replacement.gguf", route_seq=8)
    assert [response.status_code for response in responses] == [200]
    assert [call["model"] for call in calls] == ["Concrete.gguf"]
    response = client.post("/v1/chat/completions", json={
        "model": "ods/current", "messages": [{"role": "user", "content": "synthetic"}],
    })
    assert response.status_code == 200
    assert [call["model"] for call in calls] == ["Concrete.gguf", "Replacement.gguf"]
