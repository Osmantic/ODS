"""Tests for Milestone PR 4: Figma-style In-App Native Software Updater."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import main
import security
from native_updater import (
    _extract_safe_zip,
    apply_staged_update,
    get_native_updater_state,
    reset_native_updater_state,
    stage_native_update,
)


def _make_zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class _FakeStreamResponse:
    def __init__(self, payload: bytes, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"content-length": str(len(payload))}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_bytes(self, chunk_size: int = 65536):
        for i in range(0, len(self._payload), chunk_size):
            yield self._payload[i : i + chunk_size]


class _FakeAsyncClient:
    def __init__(self, payload: bytes, status_code: int = 200, **kwargs):
        self._payload = payload
        self._status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method: str, url: str):
        return _FakeStreamResponse(self._payload, self._status_code)


def test_stage_and_apply_native_update_preserves_owner_env(tmp_path, monkeypatch):
    """Valid update stages with SHA-256 check, swaps atomically, and preserves owner .env keys."""
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "WINDOWS_DESKTOP_PROFILE", True)
    reset_native_updater_state("3.0.0")

    env_path = tmp_path / ".env"
    env_path.write_text(
        "ODS_VERSION=3.0.0\nDASHBOARD_PASSWORD=my-secret-pass\nLLM_MODEL=qwen3.5:2b\n",
        encoding="utf-8",
    )

    app_dir = tmp_path / "app" / "current"
    app_dir.mkdir(parents=True)
    (app_dir / "ODS.exe").write_bytes(b"OLD_BINARY_V300")

    zip_bytes = _make_zip_bytes(
        {
            "ODS.exe": b"NEW_BINARY_V310",
            "dashboard/index.html": b"<html>v3.1.0</html>",
        }
    )
    sha256_hex = hashlib.sha256(zip_bytes).hexdigest()

    with patch("native_updater.httpx.AsyncClient", lambda **kw: _FakeAsyncClient(zip_bytes, **kw)):
        state = asyncio.run(
            stage_native_update(
                target_version="v3.1.0",
                download_url="https://releases.open-dev-society.org/ods-3.1.0.zip",
                expected_sha256=sha256_hex,
                current_version="3.0.0",
            )
        )

    assert state["status"] == "staged"
    assert state["targetVersion"] == "3.1.0"
    assert state["progressPercent"] == 100

    applied = apply_staged_update(
        app_dir,
        verify_callback=lambda p: (p / "ODS.exe").read_bytes() == b"NEW_BINARY_V310",
    )
    assert applied["status"] == "applied"
    assert applied["currentVersion"] == "3.1.0"
    assert (app_dir / "ODS.exe").read_bytes() == b"NEW_BINARY_V310"
    assert (app_dir / "dashboard" / "index.html").read_text(encoding="utf-8") == "<html>v3.1.0</html>"

    # Verify owner .env preserved while ODS_VERSION advanced
    env_text = env_path.read_text(encoding="utf-8")
    assert "ODS_VERSION=3.1.0" in env_text
    assert "DASHBOARD_PASSWORD=my-secret-pass" in env_text
    assert "LLM_MODEL=qwen3.5:2b" in env_text


def test_stage_rejects_sha256_mismatch(tmp_path, monkeypatch):
    """Tampered archive fails SHA-256 verification and is unlinked."""
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    reset_native_updater_state("3.0.0")

    zip_bytes = _make_zip_bytes({"ODS.exe": b"TAMPERED"})
    wrong_sha = "a" * 64

    with patch("native_updater.httpx.AsyncClient", lambda **kw: _FakeAsyncClient(zip_bytes, **kw)):
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            asyncio.run(
                stage_native_update(
                    target_version="3.1.0",
                    download_url="https://releases.open-dev-society.org/ods-3.1.0.zip",
                    expected_sha256=wrong_sha,
                )
            )

    state = get_native_updater_state()
    assert state["status"] == "failed"
    assert "SHA-256 mismatch" in (state["error"] or "")


def test_extract_safe_zip_rejects_path_traversal(tmp_path):
    """Zip entries attempting directory traversal are rejected before extraction."""
    evil_zip = _make_zip_bytes({"../outside.txt": b"evil"})
    with pytest.raises(ValueError, match="Unsafe entry"):
        _extract_safe_zip(evil_zip, tmp_path / "dest")


def test_apply_rolls_back_automatically_on_verification_failure(tmp_path, monkeypatch):
    """If post-swap verification fails, the previous version is automatically restored."""
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    reset_native_updater_state("3.0.0")

    app_dir = tmp_path / "app" / "current"
    app_dir.mkdir(parents=True)
    (app_dir / "ODS.exe").write_bytes(b"WORKING_V300")

    zip_bytes = _make_zip_bytes({"ODS.exe": b"BROKEN_V310"})
    sha256_hex = hashlib.sha256(zip_bytes).hexdigest()

    with patch("native_updater.httpx.AsyncClient", lambda **kw: _FakeAsyncClient(zip_bytes, **kw)):
        asyncio.run(
            stage_native_update(
                target_version="3.1.0",
                download_url="https://releases.open-dev-society.org/ods-3.1.0.zip",
                expected_sha256=sha256_hex,
                current_version="3.0.0",
            )
        )

    with pytest.raises(RuntimeError, match="automatically rolled back"):
        apply_staged_update(app_dir, verify_callback=lambda p: False)

    state = get_native_updater_state()
    assert state["status"] == "rolled_back"
    # Original binary was restored!
    assert (app_dir / "ODS.exe").read_bytes() == b"WORKING_V300"


def test_updater_router_endpoints(tmp_path, monkeypatch):
    """FastAPI endpoints /api/update/status, /api/update/native/stage, and /api/update/native/apply work end-to-end."""
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "WINDOWS_DESKTOP_PROFILE", True)
    reset_native_updater_state("3.0.0")

    (tmp_path / ".env").write_text("ODS_VERSION=3.0.0\n", encoding="utf-8")
    app_dir = tmp_path / "app" / "current"
    app_dir.mkdir(parents=True)
    (app_dir / "ODS.exe").write_bytes(b"V300")

    zip_bytes = _make_zip_bytes({"ODS.exe": b"V320"})
    sha256_hex = hashlib.sha256(zip_bytes).hexdigest()

    client = TestClient(main.app)
    headers = {"Authorization": f"Bearer {security.DASHBOARD_API_KEY}"}

    status_resp = client.get("/api/update/status", headers=headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["currentVersion"] == "3.0.0"

    with patch("native_updater.httpx.AsyncClient", lambda **kw: _FakeAsyncClient(zip_bytes, **kw)):
        stage_resp = client.post(
            "/api/update/native/stage",
            headers=headers,
            json={
                "targetVersion": "3.2.0",
                "downloadUrl": "https://releases.open-dev-society.org/ods-3.2.0.zip",
                "expectedSha256": sha256_hex,
            },
        )
    assert stage_resp.status_code == 200
    assert stage_resp.json()["status"] == "staged"

    apply_resp = client.post(
        "/api/update/native/apply",
        headers=headers,
        json={"appDir": str(app_dir)},
    )
    assert apply_resp.status_code == 200
    assert apply_resp.json()["status"] == "applied"
    assert (app_dir / "ODS.exe").read_bytes() == b"V320"
