"""Tests for Windows Desktop Portal integration, context limits, chat deletion, and model management."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Response

import config
from context_budget import plan_context_budget
import native_llm
from routers import models, pixel, pixel_images


@pytest.mark.asyncio
async def test_stream_native_chat_completion_token_limit_notice():
    """Verify stream_native_chat_completion emits continuation hint when llama-server finishes with length."""
    fake_lines = [
        'data: {"choices": [{"delta": {"content": "const hp = 100;"}, "finish_reason": null}]}',
        'data: {"choices": [{"delta": {"content": "const dead = false;"}, "finish_reason": "length"}]}',
        "data: [DONE]",
    ]

    class FakeStreamResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def aiter_lines(self):
            for line in fake_lines:
                yield line

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def stream(self, *args, **kwargs):
            return FakeStreamResponse()

    messages = [{"role": "user", "content": "Write game code"}]
    with patch("native_llm.httpx.AsyncClient", FakeAsyncClient):
        chunks = []
        async for chunk in native_llm.stream_native_chat_completion(messages, context_window=8192):
            chunks.append(chunk.decode("utf-8"))

    joined = "".join(chunks)
    assert "Response reached token limit" in joined
    assert '"finish_reason": "length"' in joined
    assert "data: [DONE]" in joined


def test_context_budget_expanded_reservation():
    """Verify context budgeting reserves up to 4096 tokens when context window allows."""
    history = [{"role": "user", "content": "Hello"}]
    budget = plan_context_budget(history, context_window=8192)
    assert budget.max_completion_tokens == 4096

    budget_large = plan_context_budget(history, context_window=16384)
    assert budget_large.max_completion_tokens == 4096


@pytest.mark.asyncio
async def test_chat_deletion_succeeds_on_windows_desktop(monkeypatch):
    """Verify chat deletion succeeds cleanly on Windows Desktop without 503 error."""
    monkeypatch.setattr(config, "WINDOWS_DESKTOP_PROFILE", True)
    monkeypatch.setattr(pixel, "WINDOWS_DESKTOP_PROFILE", True)
    monkeypatch.setattr(pixel_images, "WINDOWS_DESKTOP_PROFILE", True)
    monkeypatch.setattr(pixel_images, "conversation_storage", AsyncMock(return_value={"completed": False}))
    monkeypatch.setattr(pixel, "_pixel_config", lambda: None)

    resp = Response()
    result = await pixel_images.delete_conversation_images("chat_test_123", resp, owner="test-owner")
    assert result == {"schemaVersion": 1, "deleted": True}


def test_models_management_proof_on_windows_desktop(monkeypatch):
    """Verify _model_management_proof reports managed=True and running=True when health is ok."""
    monkeypatch.setattr(config, "WINDOWS_DESKTOP_PROFILE", True)
    fake_resp = MagicMock(status_code=200)

    with patch("httpx.Client.get", return_value=fake_resp):
        proof = models._model_management_proof()
        assert proof["managed"] is True
        assert proof["canActivate"] is True
        assert proof["running"] is True


def test_models_download_status_on_windows_desktop_idle(monkeypatch, tmp_path):
    """Verify model_download_status returns clean idle status on Windows Desktop."""
    monkeypatch.setattr(config, "WINDOWS_DESKTOP_PROFILE", True)
    monkeypatch.setattr(models, "DATA_DIR", str(tmp_path))

    status = models.model_download_status(api_key="test-key")
    assert status.get("status") == "idle"
    assert status.get("active") is False
    assert status.get("isDownloading") is False


def test_models_activate_on_windows_desktop(monkeypatch, tmp_path):
    """Verify load_model activates local model on Windows Desktop without host-agent."""
    monkeypatch.setattr(config, "WINDOWS_DESKTOP_PROFILE", True)
    monkeypatch.setattr(models, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        models,
        "_find_loadable_model",
        lambda m: {"id": m, "name": "Qwen 3.5 2B", "gguf": "Qwen3.5-2B-Q4_K_M.gguf"},
    )
    fake_installed = [
        {"filename": "Qwen3.5-2B-Q4_K_M.gguf", "path": str(tmp_path / "Qwen3.5-2B-Q4_K_M.gguf")}
    ]
    with patch("native_llm.list_local_gguf_models", return_value=fake_installed):
        result = models.load_model("qwen3.5-2b-q4", None, api_key="test-key")
        assert result.get("status") == "reloaded"
        assert result.get("model") == "Qwen3.5-2B-Q4_K_M.gguf"

        selection_file = tmp_path / "selected-model.json"
        assert selection_file.exists()
        saved = json.loads(selection_file.read_text(encoding="utf-8"))
        assert saved["ggufFile"] == "Qwen3.5-2B-Q4_K_M.gguf"


@pytest.mark.asyncio
async def test_pixel_example_projects_on_windows_desktop(monkeypatch, tmp_path):
    """Verify /example-projects scans local workspace root on Windows Desktop."""
    monkeypatch.setattr(config, "WINDOWS_DESKTOP_PROFILE", True)
    monkeypatch.setattr(pixel, "WINDOWS_DESKTOP_PROFILE", True)
    monkeypatch.setattr(pixel, "_pixel_config", lambda: None)

    proj_dir = tmp_path / "elf-of-doom"
    proj_dir.mkdir(parents=True)
    (proj_dir / "index.html").write_text("<h1>Elf of Doom</h1>", encoding="utf-8")

    with patch("native_llm.get_workspace_root", return_value=tmp_path):
        result = await pixel.pixel_example_projects()
        assert result.get("schemaVersion") == 1
        projects = result.get("projects")
        assert isinstance(projects, list)
        assert "elf-of-doom" in projects


@pytest.mark.asyncio
async def test_native_model_storage_status(monkeypatch, tmp_path):
    """Verify _model_storage_status returns real disk stats on Windows Desktop."""
    monkeypatch.setattr(config, "WINDOWS_DESKTOP_PROFILE", True)
    monkeypatch.setattr("native_downloader.get_models_directory", lambda: tmp_path)

    storage = await models._model_storage_status()
    assert storage is not None
    assert "freeBytes" in storage
    assert "totalBytes" in storage
    assert storage["freeBytes"] > 0


def test_native_model_download_dispatch(monkeypatch, tmp_path):
    """Verify download_model dispatches to native downloader on Windows Desktop."""
    monkeypatch.setattr(config, "WINDOWS_DESKTOP_PROFILE", True)
    monkeypatch.setattr(models, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        models,
        "_find_model_in_library",
        lambda m: {
            "id": m,
            "gguf_file": "TestModel-Q4.gguf",
            "gguf_url": "https://example.com/test.gguf",
            "gguf_sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        },
    )

    with patch("native_downloader.start_native_model_download") as mock_start:
        mock_start.return_value = {"status": "starting", "model": "TestModel-Q4.gguf", "active": True}
        result = models.download_model("test-model", api_key="test-key")
        assert result["status"] == "starting"
        assert result["model"] == "TestModel-Q4.gguf"
        mock_start.assert_called_once()


def test_native_model_delete_dispatch(monkeypatch, tmp_path):
    """Verify delete_model dispatches to native downloader on Windows Desktop."""
    monkeypatch.setattr(config, "WINDOWS_DESKTOP_PROFILE", True)
    monkeypatch.setattr(models, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        models,
        "_find_model_in_library",
        lambda m: {"id": m, "gguf_file": "TestModel-Q4.gguf"},
    )

    with patch("native_downloader.delete_native_model_file") as mock_del:
        mock_del.return_value = {"status": "deleted", "deletedFiles": 1}
        result = models.delete_model("test-model", api_key="test-key")
        assert result["status"] == "deleted"
        mock_del.assert_called_once_with("TestModel-Q4.gguf", None)


def test_create_workspace_preview_record():
    """Verify create_workspace_preview_record produces schemaVersion 1 compliant preview and manifest."""
    files = [
        {"path": "index.html", "sizeBytes": 120, "sha256": "a" * 64},
        {"path": "game.js", "sizeBytes": 300, "sha256": "b" * 64},
    ]
    preview, manifest = native_llm.create_workspace_preview_record(files, listener_port=3101)

    # Check preview keys and constraints required by Portal's parseVerifiedPreviewFrame
    assert preview["schemaVersion"] == 1
    assert preview["kind"] == "ods-pixel-workspace-preview"
    assert preview["port"] == 3101
    assert preview["relativeDirectory"] == "workspace"
    assert preview["files"] == 2
    assert preview["bytes"] == 420
    assert len(preview["sha256"]) == 64
    assert len(preview["entrySha256"]) == 64
    assert preview["siteId"].startswith("site-")
    assert len(preview["siteId"]) == 29
    assert preview["url"] == f"http://{preview['siteId']}.localhost:3101/{preview['siteId']}/"

    # Check manifest
    assert manifest["schemaVersion"] == 1
    assert manifest["siteId"] == preview["siteId"]
    assert manifest["sha256"] == preview["sha256"]
    assert manifest["bytes"] == 420
    assert len(manifest["files"]) == 2


@pytest.mark.asyncio
async def test_native_front_pixel_preview_serving(tmp_path, monkeypatch):
    """Verify _handle_pixel_preview serves workspace files and manifests."""
    import native_front

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "index.html").write_text("<h1>Hello Game</h1>", encoding="utf-8")

    manifest_data = {
        "schemaVersion": 1,
        "siteId": "site-" + "c" * 24,
        "sha256": "d" * 64,
        "bytes": 19,
        "files": [{"path": "index.html", "bytes": 19, "sha256": "e" * 64}],
    }
    (tmp_path / "workspace_manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    monkeypatch.setenv("ODS_DATA_DIR", str(tmp_path))
    import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))

    sent_messages = []

    async def fake_send(msg):
        sent_messages.append(msg)

    # 1. Manifest request
    manifest_path = f"/pixel-preview/site-{'c' * 24}/__ods_manifest__.json"
    handled = await native_front._handle_pixel_preview({"type": "http"}, fake_send, manifest_path)
    assert handled is True
    assert any(m.get("status") == 200 for m in sent_messages)
    assert any(b"Hello Game" not in m.get("body", b"") and b"workspace_manifest" not in m.get("body", b"") and b'"schemaVersion": 1' in m.get("body", b"") for m in sent_messages)

    # 2. HTML index request
    sent_messages.clear()
    index_path = f"/pixel-preview/site-{'c' * 24}/index.html"
    handled = await native_front._handle_pixel_preview({"type": "http"}, fake_send, index_path)
    assert handled is True
    assert any(m.get("status") == 200 for m in sent_messages)
    assert any(b"<h1>Hello Game</h1>" in m.get("body", b"") for m in sent_messages)

    # 3. Path traversal returns 404
    sent_messages.clear()
    traversal_path = f"/pixel-preview/site-{'c' * 24}/../../secret.txt"
    handled = await native_front._handle_pixel_preview({"type": "http"}, fake_send, traversal_path)
    assert handled is True
    assert any(m.get("status") == 404 for m in sent_messages)


@pytest.mark.asyncio
async def test_windows_desktop_model_profile_and_recovery(monkeypatch):
    """Verify model profile and recovery endpoints return safe defaults on Windows Desktop."""
    monkeypatch.setattr(config, "WINDOWS_DESKTOP_PROFILE", True)

    profile = await models.model_profile("qwen3.5-2b", api_key="test-key")
    assert profile == {"mode": "off", "modelId": "qwen3.5-2b", "profile": None}

    recheck = models.recheck_model_profile("qwen3.5-2b", api_key="test-key")
    assert recheck == {"status": "skipped", "modelId": "qwen3.5-2b"}

    recovery = models.model_recovery_status(api_key="test-key")
    assert json.loads(recovery.body) == {"status": "none", "recoverable": False}


@pytest.mark.asyncio
async def test_windows_desktop_list_models_and_download_status(monkeypatch):
    """Verify list_models and download_status succeed cleanly with native downloader."""
    monkeypatch.setattr(config, "WINDOWS_DESKTOP_PROFILE", True)
    monkeypatch.setattr(config, "INSTALL_DIR", "C:\\Users\\pratham\\ods")
    monkeypatch.setattr(config, "DATA_DIR", "C:\\Users\\pratham\\ods\\data")

    # 1. Download status
    status = models.model_download_status(api_key="test-key")
    assert isinstance(status, dict)
    assert "status" in status
    assert status.get("active") in (True, False)

    # 2. List models
    catalog = await models.list_models(api_key="test-key")
    assert isinstance(catalog, dict)
    assert "models" in catalog
    assert len(catalog["models"]) > 0
    assert catalog.get("odsMode") == "local"


