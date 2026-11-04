"""Windows Desktop local chat: streamed answers, honest states, fitted context.

llama-server is a httpx.MockTransport that implements the b9014 endpoints the
router uses and records every request it receives.
"""

import json
import math
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import native_llm
from native_llm import NativeLlm
from routers import local_chat

AUTH = {"Authorization": "Bearer test-key-12345"}
SECRET = "llama-" + "s" * 24  # a fixture, kept literal-free for gitleaks


class FakeLlama:
    def __init__(self, n_ctx=4096, health=200, chat_status=200, chat_lines=None, offline=False, fail_mid_stream=False):
        self.n_ctx, self.health, self.chat_status = n_ctx, health, chat_status
        self.chat_lines = chat_lines if chat_lines is not None else [
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            'data: [DONE]',
        ]
        self.offline, self.fail_mid_stream = offline, fail_mid_stream
        self.requests = []
        self.sent_messages = None

    def handler(self, request):
        self.requests.append(request)
        if self.offline:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        if path == "/health":
            return httpx.Response(self.health, json={"status": "ok"})
        if request.headers.get("authorization") != f"Bearer {SECRET}":
            return httpx.Response(401, json={"error": "invalid api key"})
        if path == "/props":
            return httpx.Response(200, json={"default_generation_settings": {"n_ctx": self.n_ctx}})
        if path == "/apply-template":
            messages = json.loads(request.content)["messages"]
            return httpx.Response(200, json={"prompt": "".join(f"<{m['role']}>{m['content']}" for m in messages)})
        if path == "/tokenize":
            content = json.loads(request.content)["content"]
            return httpx.Response(200, json={"tokens": [0] * math.ceil(len(content) / 4)})
        if path == "/v1/chat/completions":
            self.sent_messages = json.loads(request.content)["messages"]
            if self.chat_status != 200:
                return httpx.Response(self.chat_status, json={"error": "bad"})
            if self.fail_mid_stream:
                async def broken():
                    yield b'data: {"choices":[{"delta":{"content":"par"}}]}\n\n'
                    raise httpx.ReadError("connection reset", request=request)
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=broken())
            body = "".join(line + "\n\n" for line in self.chat_lines).encode()
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)
        return httpx.Response(404)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    llm = NativeLlm("http://127.0.0.1:18080", tmp_path)
    llm.key_file.write_text(SECRET, encoding="utf-8")
    monkeypatch.setattr(native_llm, "configured", lambda: llm)
    # Long past sign-in unless a test says otherwise.
    monkeypatch.setattr(local_chat, "_STARTED_AT", time.monotonic() - 3600)
    state = {}

    def install(fake):
        state["fake"] = fake
        real = httpx.AsyncClient
        monkeypatch.setattr(local_chat.httpx, "AsyncClient",
                            lambda **kwargs: real(transport=httpx.MockTransport(fake.handler), **kwargs))
        app = FastAPI()
        app.include_router(local_chat.router)
        return TestClient(app), fake

    return install, tmp_path


def events(text):
    return [block for block in text.split("\n\n") if block.strip()]


def test_a_message_streams_back_with_the_context_note_first(setup):
    install, _ = setup
    client, fake = install(FakeLlama())
    response = client.post("/api/local-chat/stream", headers=AUTH,
                           json={"messages": [{"role": "user", "content": "Say hello"}]})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    blocks = events(response.text)
    assert blocks[0].startswith("event: ods") and json.loads(blocks[0].split("data: ", 1)[1])["droppedMessages"] == 0
    assert blocks[-1] == "data: [DONE]" and "Hel" in response.text and "lo" in response.text
    assert fake.sent_messages == [{"role": "user", "content": "Say hello"}]


def test_the_key_goes_to_llama_server_but_never_to_the_browser(setup, caplog):
    install, _ = setup
    client, fake = install(FakeLlama())
    response = client.post("/api/local-chat/stream", headers=AUTH,
                           json={"messages": [{"role": "user", "content": "hi"}]})
    keyed = [r for r in fake.requests if r.url.path != "/health"]
    assert keyed and all(r.headers["authorization"] == f"Bearer {SECRET}" for r in keyed)
    assert SECRET not in response.text and SECRET not in str(response.headers) and SECRET not in caplog.text


def test_a_long_conversation_on_a_small_context_still_answers(setup):
    install, _ = setup
    client, fake = install(FakeLlama(n_ctx=4096))
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " * 40} for i in range(399)]
    response = client.post("/api/local-chat/stream", headers=AUTH, json={"messages": history})
    note = json.loads(events(response.text)[0].split("data: ", 1)[1])
    assert response.status_code == 200 and note["droppedMessages"] > 0
    assert note["promptTokens"] <= 4096 - 1024
    assert fake.sent_messages[0]["role"] == "user" and fake.sent_messages[-1] == history[-1]


def test_the_reported_context_start_keeps_the_next_prompt_prefix(setup):
    install, _ = setup
    client, fake = install(FakeLlama(n_ctx=4096))
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " * 40} for i in range(199)]
    first = client.post("/api/local-chat/stream", headers=AUTH, json={"messages": history})
    start = json.loads(events(first.text)[0].split("data: ", 1)[1])["contextStart"]
    first_prefix = fake.sent_messages[:-1]
    history += [{"role": "assistant", "content": "ok"}, {"role": "user", "content": "next"}]
    second = client.post("/api/local-chat/stream", headers=AUTH, json={"messages": history, "contextStart": start})
    assert json.loads(events(second.text)[0].split("data: ", 1)[1])["contextStart"] == start
    assert fake.sent_messages[: len(first_prefix)] == first_prefix


def test_a_message_too_long_is_refused_before_inference(setup):
    install, _ = setup
    client, fake = install(FakeLlama(n_ctx=4096))
    response = client.post("/api/local-chat/stream", headers=AUTH,
                           json={"messages": [{"role": "user", "content": "x" * 30000}]})
    body = response.json()["error"]
    assert response.status_code == 413 and body["code"] == "message_too_long"
    assert body["budgetTokens"] == 3072 and body["promptTokens"] > 3072
    assert fake.sent_messages is None


@pytest.mark.parametrize("fake, code, status", [
    (FakeLlama(health=503), "model_loading", 503),
    (FakeLlama(offline=True), "model_unavailable", 503),
    (FakeLlama(chat_status=500), "upstream_error", 502),
])
def test_failures_have_fixed_codes_and_plain_sentences(setup, fake, code, status):
    install, _ = setup
    client, _ = install(fake)
    response = client.post("/api/local-chat/stream", headers=AUTH,
                           json={"messages": [{"role": "user", "content": "hi"}]})
    error = response.json()["error"]
    assert response.status_code == status and error["code"] == code
    assert error["message"] == local_chat._MESSAGES[code]


def test_a_stopped_or_failed_runtime_is_explained(setup):
    install, runtime = setup
    client, _ = install(FakeLlama(offline=True))
    (runtime / "intent.json").write_text('{"State":"stopped"}', encoding="utf-8")
    assert client.get("/api/local-chat/status", headers=AUTH).json()["state"] == "model_stopped"
    (runtime / "intent.json").write_text('{"State":"running"}', encoding="utf-8")
    (runtime / "ready.json").write_text('{"Error":"Vulkan device lost","Code":"device_lost"}', encoding="utf-8")
    assert client.get("/api/local-chat/status", headers=AUTH).json()["state"] == "model_failed"


def test_a_silent_model_right_after_sign_in_is_starting_not_broken(setup, monkeypatch):
    install, runtime = setup
    client, _ = install(FakeLlama(offline=True))
    monkeypatch.setattr(local_chat, "_STARTED_AT", time.monotonic() - 20)
    status = client.get("/api/local-chat/status", headers=AUTH).json()
    assert status == {"state": "model_starting", "message": local_chat._MESSAGES["model_starting"]}
    response = client.post("/api/local-chat/stream", headers=AUTH, json={"messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 503 and response.json()["error"]["code"] == "model_starting"
    # A recorded stop or failure is still said at once.
    (runtime / "ready.json").write_text('{"Error":"Vulkan device lost"}', encoding="utf-8")
    assert client.get("/api/local-chat/status", headers=AUTH).json()["state"] == "model_failed"
    (runtime / "intent.json").write_text('{"State":"stopped"}', encoding="utf-8")
    assert client.get("/api/local-chat/status", headers=AUTH).json()["state"] == "model_stopped"
    # After the start window, silence means the model is not answering.
    (runtime / "ready.json").unlink()
    (runtime / "intent.json").write_text('{"State":"running"}', encoding="utf-8")
    monkeypatch.setattr(local_chat, "_STARTED_AT", time.monotonic() - local_chat._START_GRACE_SECONDS - 1)
    assert client.get("/api/local-chat/status", headers=AUTH).json()["state"] == "model_unavailable"


def test_ready_status_reports_model_and_context(setup):
    install, runtime = setup
    client, _ = install(FakeLlama(n_ctx=8192))
    (runtime / "ready.json").write_text('{"ModelId":"Qwen3.5-2B-Q4_K_M.gguf","Port":18080}', encoding="utf-8")
    assert client.get("/api/local-chat/status", headers=AUTH).json() == {
        "state": "ready", "model": "Qwen3.5-2B-Q4_K_M.gguf", "contextSize": 8192}


def test_a_dropped_connection_mid_answer_ends_with_an_error_event(setup):
    install, _ = setup
    client, _ = install(FakeLlama(fail_mid_stream=True))
    response = client.post("/api/local-chat/stream", headers=AUTH,
                           json={"messages": [{"role": "user", "content": "hi"}]})
    blocks = events(response.text)
    assert "par" in response.text and '"model_unavailable"' in blocks[-2] and blocks[-1] == "data: [DONE]"


def test_an_oversized_stream_line_is_cut_off(setup):
    install, _ = setup
    client, _ = install(FakeLlama(chat_lines=["data: " + "x" * (local_chat._MAX_SSE_LINE_BYTES + 10)]))
    response = client.post("/api/local-chat/stream", headers=AUTH,
                           json={"messages": [{"role": "user", "content": "hi"}]})
    assert '"upstream_error"' in response.text and "x" * 1000 not in response.text


def test_the_last_message_must_be_the_users(setup):
    install, _ = setup
    client, _ = install(FakeLlama())
    response = client.post("/api/local-chat/stream", headers=AUTH, json={"messages": [
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]})
    assert response.status_code == 422


def test_requests_need_the_dashboard_key(setup):
    install, _ = setup
    client, _ = install(FakeLlama())
    assert client.get("/api/local-chat/status").status_code == 401
