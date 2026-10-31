"""Tests for Milestone PR 3: native local chat, context budgeting, document extraction, workspace files, and Portal/Voice bridges."""

from __future__ import annotations

import sys
import zlib
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

DASHBOARD_API_DIR = Path(__file__).resolve().parents[1]
if str(DASHBOARD_API_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_API_DIR))


def test_context_budget_preserves_kv_prefix_and_advances_cleanly():
    import context_budget

    history = [
        {"role": "user", "content": "Hello, I am building a Windows app."},
        {"role": "assistant", "content": "Great! How can I help you architect it?"},
        {"role": "user", "content": "What is DirectX UserGpuPreferences?"},
        {"role": "assistant", "content": "It is a registry key under HKCU\\Software\\Microsoft\\DirectX."},
    ]
    # 1. Plenty of context -> context_start stays at 0
    b1 = context_budget.plan_context_budget(
        history,
        system_prompt="You are ODS.",
        context_window=4096,
        context_start=0,
        reserve_output_tokens=512,
    )
    assert b1.context_start == 0
    assert b1.truncated is False
    assert b1.dropped_messages == 0
    assert b1.messages[0]["role"] == "system"
    assert len(b1.messages) == 5

    # 2. Follow-up turn within budget keeps the exact same context_start=0
    history_followup = history + [
        {"role": "user", "content": "How does GpuPreference=2 work?"},
    ]
    b2 = context_budget.plan_context_budget(
        history_followup,
        system_prompt="You are ODS.",
        context_window=4096,
        context_start=b1.context_start,
        reserve_output_tokens=512,
    )
    assert b2.context_start == 0
    assert b2.messages[:5] == b1.messages

    # 3. Overflowing the usable budget advances context_start to a user turn boundary
    long_history = []
    for i in range(20):
        long_history.append({"role": "user", "content": f"Turn {i}: " + ("word " * 120)})
        long_history.append({"role": "assistant", "content": f"Reply {i}: " + ("response " * 120)})
    b3 = context_budget.plan_context_budget(
        long_history,
        system_prompt="You are ODS.",
        context_window=2048,
        context_start=0,
        reserve_output_tokens=512,
    )
    assert b3.context_start > 0
    assert b3.truncated is True
    assert long_history[b3.context_start]["role"] == "user"
    assert b3.estimated_prompt_tokens <= b3.context_window

    # 4. Next short turn after advancing keeps the new context_start stable!
    long_history_next = long_history + [{"role": "user", "content": "Short follow-up?"}]
    b4 = context_budget.plan_context_budget(
        long_history_next,
        system_prompt="You are ODS.",
        context_window=2048,
        context_start=b3.context_start,
        reserve_output_tokens=512,
    )
    assert b4.context_start == b3.context_start


def test_single_huge_message_is_truncated_within_budget():
    import context_budget

    huge = [{"role": "user", "content": "A" * 50_000}]
    budgeted = context_budget.plan_context_budget(
        huge,
        system_prompt="You are ODS.",
        context_window=2048,
        context_start=0,
        reserve_output_tokens=512,
    )
    assert budgeted.truncated is True
    assert budgeted.estimated_prompt_tokens <= budgeted.context_window
    assert "[... content truncated to fit model context window ...]" in budgeted.messages[-1]["content"]


def test_document_and_pdf_text_extraction():
    import native_llm

    # 1. Markdown/code extraction
    md_res = native_llm.extract_document_text(
        "README.md",
        b"# ODS Windows Native\nRuns without Docker or WSL.",
        "text/markdown",
    )
    assert md_res["filename"] == "README.md"
    assert "Runs without Docker or WSL." in md_res["text"]
    assert md_res["truncated"] is False

    # 2. Compressed PDF stream extraction
    pdf_stream = zlib.compress(b"BT /F1 12 Tf (Hello from native Windows PDF!) Tj ET")
    fake_pdf = (
        b"%PDF-1.4\n1 0 obj\n<< /Length "
        + str(len(pdf_stream)).encode("ascii")
        + b" /Filter /FlateDecode >>\nstream\n"
        + pdf_stream
        + b"\nendstream\nendobj\n%%EOF"
    )
    pdf_res = native_llm.extract_document_text("sample.pdf", fake_pdf, "application/pdf")
    assert pdf_res["filename"] == "sample.pdf"
    assert "Hello from native Windows PDF!" in pdf_res["text"]

    # 3. Binary file disguised as .txt is rejected
    with pytest.raises(ValueError, match="binary file"):
        native_llm.extract_document_text("bad.txt", b"\x00\x01\x02binary", "text/plain")


def test_workspace_file_tool_and_traversal_defense(tmp_path, monkeypatch):
    import config
    import native_llm

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))

    reply = (
        "Here is your script:\n"
        '<ods_write_file path="notes/hello.txt">\n'
        "Hello Windows Native!\n"
        "</ods_write_file>\n"
        '<ods_write_file path="../escape.txt">bad</ods_write_file>'
    )
    delivered = native_llm.execute_workspace_file_blocks(reply)
    assert len(delivered) == 1
    assert delivered[0]["path"] == "notes/hello.txt"
    written = tmp_path / "workspace" / "notes" / "hello.txt"
    assert written.is_file()
    assert written.read_text(encoding="utf-8").strip() == "Hello Windows Native!"
    assert not (tmp_path / "escape.txt").exists()


def test_local_chat_router_and_portal_bridge_in_windows_desktop(tmp_path, monkeypatch):
    import config
    import main
    import routers.pixel as pixel_router
    import security

    monkeypatch.setenv("ODS_RUNTIME_PROFILE", "windows-desktop")
    monkeypatch.setenv("ODS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-desktop-key-999")
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", "test-desktop-key-999")
    monkeypatch.setattr(config, "RUNTIME_PROFILE", "windows-desktop")
    monkeypatch.setattr(config, "WINDOWS_DESKTOP_PROFILE", True)
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(pixel_router, "WINDOWS_DESKTOP_PROFILE", True)

    headers = {"Authorization": "Bearer test-desktop-key-999"}

    # Mock native llama-server responses for /health, /v1/models, /props, and /v1/chat/completions
    async def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={"data": [{"id": "Qwen3-8B-Q4_K_M.gguf"}]},
            )
        if request.url.path == "/props":
            return httpx.Response(
                200,
                json={
                    "default_generation_settings": {"n_ctx": 16384},
                    "modalities": {"vision": True},
                },
            )
        if request.url.path == "/v1/chat/completions":
            sse_payload = (
                'data: {"choices":[{"delta":{"content":"Hello from "}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"GTX 1650! "}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"<ods_write_file path=\\"out.txt\\">Saved!</ods_write_file>"}}]}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse_payload.encode("utf-8"),
            )
        return httpx.Response(404)

    mock_transport = httpx.MockTransport(mock_handler)
    orig_async_client = httpx.AsyncClient

    def patched_async_client(*args, **kwargs):
        kwargs["transport"] = mock_transport
        return orig_async_client(*args, **kwargs)

    with patch("native_llm.httpx.AsyncClient", side_effect=patched_async_client):
        with TestClient(main.app, base_url="http://127.0.0.1:3101") as client:
            # 1. /api/local-chat/status
            st = client.get("/api/local-chat/status", headers=headers)
            assert st.status_code == 200
            st_data = st.json()
            assert st_data["available"] is True
            assert st_data["model"] == "Qwen3-8B-Q4_K_M.gguf"
            assert st_data["contextLength"] == 16384
            assert st_data["visionSupported"] is True

            # 2. /api/pixel/status bridges to native llama-server in windows-desktop
            px = client.get("/api/pixel/status", headers=headers)
            assert px.status_code == 200
            px_data = px.json()
            assert px_data["available"] is True
            assert px_data["runtime"]["source"] == "windows-desktop"
            assert px_data["runtime"]["model"] == "Qwen3-8B-Q4_K_M.gguf"
            assert px_data["runtime"]["contextLength"] == 16384

            # 3. /api/voice/status reports windows-native-speech healthy
            vc = client.get("/api/voice/status", headers=headers)
            assert vc.status_code == 200
            vc_data = vc.json()
            assert vc_data["available"] is True
            assert vc_data["services"]["stt"]["provider"] == "windows-native-speech"

            # 4. /api/local-chat/conversations persistence
            put_res = client.put(
                "/api/local-chat/conversations",
                headers=headers,
                json={
                    "conversations": [
                        {
                            "id": "conv-1",
                            "title": "Windows GPU Chat",
                            "contextStart": 2,
                            "messages": [{"role": "user", "content": "Hi"}],
                        }
                    ]
                },
            )
            assert put_res.status_code == 200
            get_res = client.get("/api/local-chat/conversations", headers=headers)
            assert get_res.status_code == 200
            assert get_res.json()["conversations"][0]["contextStart"] == 2

            # 5. /api/local-chat/stream streams tokens + creates workspace file
            stream_res = client.post(
                "/api/local-chat/stream",
                headers=headers,
                json={
                    "messages": [{"role": "user", "content": "Create out.txt"}],
                    "contextStart": 0,
                },
            )
            assert stream_res.status_code == 200
            body_text = stream_res.text
            assert "Hello from " in body_text
            assert "GTX 1650!" in body_text
            assert '"odsMeta"' in body_text
            assert "data: [DONE]" in body_text

            # 6. Verify workspace file created during stream is downloadable
            wf_list = client.get("/api/local-chat/workspace/files", headers=headers)
            assert wf_list.status_code == 200
            assert any(f["path"] == "out.txt" for f in wf_list.json()["files"])
            wf_dl = client.get("/api/local-chat/workspace/files/out.txt", headers=headers)
            assert wf_dl.status_code == 200
            assert wf_dl.text == "Saved!"
