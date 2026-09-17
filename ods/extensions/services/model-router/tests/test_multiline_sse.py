"""Public forwarding through a real chunked HTTP upstream with SSE data fields."""

import asyncio
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from test_router import router as router, _RecordingTelemetry, _signed_marker


@pytest.fixture
def wire_router(router):
    mod, client, write_state, _ = router
    response_bytes = []
    requests = []

    class Upstream(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                # Deliberately split UTF-8, JSON tokens and CRLF boundaries.
                for byte in response_bytes[0]:
                    self.wfile.write(b"1\r\n" + bytes([byte]) + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # A pinned identity mismatch may close upstream early.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    mod.ENDPOINTS_PATH.write_text(json.dumps({"endpoints": [{
        "id": "llama-server-default", "baseUrl": f"http://127.0.0.1:{server.server_port}",
    }]}), encoding="utf-8")
    asyncio.run(mod.app.state.http.aclose())
    mod.app.state.http = httpx.AsyncClient(trust_env=False)
    mod.app.state.telemetry = _RecordingTelemetry()
    write_state()
    try:
        yield mod, client, response_bytes, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def multiline(kind, model="Concrete.gguf", newline="\n"):
    if kind == "chat":
        value = {
            "model": model,
            "choices": [{"delta": {"content": "Xin chào Concrete.gguf"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 4},
        }
    else:
        value = {
            "type": "response.completed",
            "response": {
                "model": model, "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": "Xin chào Concrete.gguf"}]}],
                "usage": {"input_tokens": 9, "output_tokens": 4},
            },
        }
    fields = ["id: sample", "event: message", "data"]
    for index, line in enumerate(json.dumps(value, indent=2, ensure_ascii=False).splitlines()):
        fields.append("data:" + (" " if index % 2 else "") + line)
        if index == 1:
            fields.extend([": upstream comment", "retry: 1000"])
    return (newline.join(fields) + newline * 2).encode()


def request_stream(client, kind, probe, **headers):
    body = {"model": "default", "stream": True}
    if kind == "chat":
        body["messages"] = [{"role": "user", "content": _signed_marker(probe)}]
    else:
        body["input"] = _signed_marker(probe)
    return client.post(
        "/v1/chat/completions" if kind == "chat" else "/v1/responses",
        json=body, headers={"Authorization": "Bearer internal-secret", **headers},
    )


@pytest.mark.parametrize("kind", ["chat", "responses"])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_complete_data_event_restores_alias_usage_and_evidence(wire_router, kind, newline):
    mod, client, response_bytes, requests = wire_router
    response_bytes.append(multiline(kind, newline=newline))
    probe = str(uuid.uuid4())
    response = request_stream(client, kind, probe)
    assert response.status_code == 200
    assert requests[0]["model"] == "Concrete.gguf"
    blocks = response.content.split((newline * 2).encode())
    assert len(blocks) == 2 and blocks[-1] == b""
    fields = blocks[0].decode().split(newline)
    data = []
    other = []
    for field in fields:
        name, _, value = field.partition(":")
        if name == "data":
            data.append(value.removeprefix(" "))
        else:
            other.append(field)
    value = json.loads("\n".join(data))
    nested = value if kind == "chat" else value["response"]
    assert nested["model"] == "default"
    assert "Xin chào Concrete.gguf" in json.dumps(value, ensure_ascii=False)
    assert other == ["id: sample", "event: message", ": upstream comment", "retry: 1000"]
    assert [(event["input_tokens"], event["output_tokens"]) for event in mod.app.state.telemetry.events] == [(9, 4)]
    evidence = client.get(f"/internal/route-evidence/{probe}", headers={"Authorization": "Bearer internal-secret"})
    assert evidence.status_code == 200
    assert evidence.json()["responseModel"] == "Concrete.gguf"
    assert mod._inflight == 0


@pytest.mark.parametrize("kind", ["chat", "responses"])
def test_pinned_multiline_identity_change_is_rejected(wire_router, kind):
    mod, client, response_bytes, _ = wire_router
    response_bytes.append(multiline(kind, model="Wrong.gguf"))
    probe = str(uuid.uuid4())
    with pytest.raises(mod.RouterError, match="Backend response identity changed"):
        request_stream(client, kind, probe, **{
            "X-ODS-Expected-Catalog": "concrete",
            "X-ODS-Expected-Model": "Concrete.gguf",
            "X-ODS-Expected-Route": "7",
        })
    evidence = client.get(f"/internal/route-evidence/{probe}", headers={"Authorization": "Bearer internal-secret"})
    assert evidence.status_code == 404
    assert mod.app.state.telemetry.events == []
    assert mod._inflight == 0


def test_done_field_inside_invalid_multiline_event_is_not_completion(wire_router):
    mod, client, response_bytes, _ = wire_router
    raw = b"data: [DONE]\ndata: not a done event\n\n"
    response_bytes.append(raw)
    probe = str(uuid.uuid4())
    response = request_stream(client, "chat", probe)
    assert response.content == raw
    evidence = client.get(f"/internal/route-evidence/{probe}", headers={"Authorization": "Bearer internal-secret"})
    assert evidence.status_code == 404
    assert mod.app.state.telemetry.events == []
    assert mod._inflight == 0
