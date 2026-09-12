"""Responses lifecycle events carry model identity inside response objects."""

import json
import uuid

import pytest

from test_router import router as router, _set_stream_upstream, _signed_marker


def event(model, kind="response.completed"):
    return (f"event: {kind}\n" + "data: " + json.dumps({
        "type": kind,
        "response": {
            "model": model, "status": "completed",
            "output": [{"content": [{"type": "output_text", "text": "Concrete.gguf is literal text"}]}],
            "usage": {"input_tokens": 9, "output_tokens": 4},
        },
    }) + "\n\n").encode()


@pytest.mark.parametrize("alias", ["ods/current", "default"])
def test_rewrites_nested_response_identity_and_records_concrete_model(router, alias):
    mod, client, write_state, _ = router
    write_state()
    probe = str(uuid.uuid4())
    raw = event("Concrete.gguf", "response.created") + event("Concrete.gguf")
    _set_stream_upstream(mod, [raw[:63], raw[63:117], raw[117:]])
    response = client.post("/v1/responses", json={
        "model": alias, "stream": True, "input": _signed_marker(probe),
    })
    assert response.status_code == 200
    values = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert [value["response"]["model"] for value in values] == [alias, alias]
    assert values[-1]["response"]["output"][0]["content"][0]["text"] == "Concrete.gguf is literal text"
    evidence = client.get(f"/internal/route-evidence/{probe}", headers={
        "Authorization": "Bearer internal-secret",
    })
    assert evidence.status_code == 200
    assert evidence.json()["responseModel"] == "Concrete.gguf"
    assert mod._inflight == 0


@pytest.mark.parametrize("models", [("Wrong.gguf",), ("Wrong.gguf", "Concrete.gguf")])
def test_nested_mismatched_identity_cannot_produce_success_evidence(router, models):
    mod, client, write_state, _ = router
    write_state()
    probe = str(uuid.uuid4())
    _set_stream_upstream(mod, [event(model) for model in models])
    response = client.post("/v1/responses", json={
        "model": "ods/current", "stream": True, "input": _signed_marker(probe),
    })
    assert response.status_code == 200
    evidence = client.get(f"/internal/route-evidence/{probe}", headers={
        "Authorization": "Bearer internal-secret",
    })
    assert evidence.status_code == 404
    assert mod._inflight == 0


def test_pinned_responses_stream_rejects_nested_identity_change(router):
    mod, client, write_state, _ = router
    write_state()
    _set_stream_upstream(mod, [event("Wrong.gguf")])
    with pytest.raises(mod.RouterError, match="Backend response identity changed"):
        client.post("/v1/responses", json={
            "model": "ods/current", "stream": True, "input": "hi",
        }, headers={
            "X-ODS-Expected-Catalog": "concrete",
            "X-ODS-Expected-Model": "Concrete.gguf",
            "X-ODS-Expected-Route": "7",
        })
    assert mod._inflight == 0
