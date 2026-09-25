"""Exercise the cookie-authenticated image route against a real streaming server."""

import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

import session_signer


def delta(text="", finish=None):
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": finish}]}) + "\n\n"


@pytest.fixture
def vision_server(monkeypatch):
    state = {"frames": [], "requests": []}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            state["requests"].append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            # Clean HTTP EOF is not proof that generation reached a terminal frame.
            self.send_header("Connection", "close")
            self.end_headers()
            for frame in state["frames"]:
                self.wfile.write(frame.encode())
                self.wfile.flush()
            self.close_connection = True

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    monkeypatch.setenv("ODS_TALK_VISION_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("ODS_TALK_VISION_MODEL", "fixture-vision")
    monkeypatch.setenv("ODS_TALK_VISION_KEY", "")
    monkeypatch.setenv("ODS_SESSION_SECRET", "vision-completion-test-secret")
    session_signer._set_secret_for_tests("vision-completion-test-secret")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def image_request(client, state):
    client.cookies.set("ods-session", session_signer.issue(ttl_seconds=3600))
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aTXkAAAAASUVORK5CYII=")
    response = client.post("/api/talk/attachment", files={"file": ("pixel.png", png, "image/png")}, data={"text": "Describe this pixel"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    path, payload = state["requests"][-1]
    assert path == "/v1/chat/completions"
    assert payload["model"] == "fixture-vision" and payload["stream"] is True
    assert payload["messages"][0]["content"][1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(png).decode()
    return [json.loads(line[5:].strip()) for line in response.text.splitlines() if line.startswith("data:")]


@pytest.mark.parametrize("frames", [[], [delta("The pixel is")], [delta("The pixel is"), 'data: {"choices":']])
def test_clean_eof_without_completion_is_an_error(test_client, vision_server, frames):
    vision_server["frames"] = frames
    events = image_request(test_client, vision_server)
    assert not any(event["type"] == "complete" for event in events)
    assert [event["type"] for event in events][-2:] == ["error", "done"]
    assert events[-2]["status_code"] == 502
    assert "before completing" in events[-2]["detail"]
    assert [event["text"] for event in events if event["type"] == "delta"] == (["The pixel is"] if frames else [])


@pytest.mark.parametrize("terminal", ["data: [DONE]\n\n", delta(finish="stop")])
def test_explicit_completion_still_finishes_the_answer(test_client, vision_server, terminal):
    vision_server["frames"] = [delta("A white pixel."), terminal]
    events = image_request(test_client, vision_server)
    assert [event["type"] for event in events] == ["session", "delta", "complete", "done"]
    assert events[-2]["text"] == "A white pixel."
    assert events[-2]["warning"] is None


def test_token_limit_is_reported_as_a_limited_answer(test_client, vision_server):
    vision_server["frames"] = [delta("A white"), delta(finish="length"), "data: [DONE]\n\n"]
    events = image_request(test_client, vision_server)
    assert [event["type"] for event in events] == ["session", "delta", "complete", "done"]
    assert events[-2]["text"] == "A white"
    assert "token limit" in (events[-2]["warning"] or "")


def test_unsigned_image_request_does_not_reach_the_vision_backend(test_client, vision_server):
    response = test_client.post("/api/talk/attachment", files={"file": ("pixel.png", b"fixture", "image/png")}, headers=test_client.auth_headers)
    assert response.status_code == 401
    assert vision_server["requests"] == []


@pytest.mark.parametrize("terminal", [
    'data: {"error":{"message":"private backend diagnostic"}}\n\n',
    delta(finish="content_filter"),
    delta(finish="tool_calls"),
])
def test_failed_generation_is_not_made_successful_by_done(test_client, vision_server, terminal):
    vision_server["frames"] = [delta("Partial"), terminal, "data: [DONE]\n\n"]
    events = image_request(test_client, vision_server)
    assert [event["type"] for event in events] == ["session", "delta", "error", "done"]
    assert events[-2]["status_code"] == 502
    assert "private backend diagnostic" not in json.dumps(events)
