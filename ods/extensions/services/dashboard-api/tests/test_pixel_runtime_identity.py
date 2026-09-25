"""Runtime availability and release identity are deliberately separate facts."""
import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_pixel import FakeResponse, FakeClient
from routers import pixel
import security
from pixel_runtime_identity import project_runtime_identity as project_runtime_identity, unknown_runtime_identity


def observed():
    value = unknown_runtime_identity()
    value.update(state="partial", diskComparison="match", reasonCode="release-binding-unavailable",
                 observedAt=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
    value["identities"].update(pluginSha256="a" * 64, openclawModuleSha256="b" * 64, openclawVersion="2026.6.33")
    return value


def test_edge_and_dashboard_share_the_projection_contract():
    filename = Path(__file__).resolve().parents[2] / "pixel-edge" / "runtime_identity.py"
    assert filename.read_text(encoding="utf-8") == (Path(__file__).resolve().parents[1] / "pixel_runtime_identity.py").read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("edge_runtime_identity", filename)
    edge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(edge)
    value = observed()
    for module in (edge, pixel):
        assert module.project_runtime_identity({**value, "privatePath": "/home/secret"}) == value
        for field, wrong in (("runtimeMatchesRelease", True), ("schemaVersion", True),
                             ("observedAt", "2001-01-01T00:00:00.000Z"), ("observedAt", "2026-99-01T00:00:00.000Z")):
            with pytest.raises(ValueError):
                module.project_runtime_identity({**value, field: wrong})
        secret = copy.deepcopy(value)
        secret["identities"]["pluginSha256"] = "/home/private/token"
        with pytest.raises(ValueError):
            module.project_runtime_identity(secret)
        false_surface = copy.deepcopy(value)
        false_surface["toolSchemas"]["offeredToolCount"] = 20
        with pytest.raises(ValueError):
            module.project_runtime_identity(false_surface)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["partial", "mismatch", "missing", "bad", "timeout", "nested"])
async def test_status_keeps_chat_available_without_promoting_partial_identity(monkeypatch, kind):
    monkeypatch.setenv("PIXEL_OPENWEBUI_KEY", "e" * 64)
    async def host(*_args, **_kwargs):
        return {"status": "idle"}
    monkeypatch.setattr(pixel, "request_agent_json", host)
    value = observed()
    if kind == "mismatch":
        value.update(state="mismatch", diskComparison="mismatch", reasonCode="runtime-files-changed", runtimeMatchesRelease=False)
    if kind == "bad":
        value.update(runtimeMatchesRelease=True, credentials="must-not-leak")
    calls = []
    class Client(FakeClient):
        def stream(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url.endswith("/v1/models"):
                self.response = FakeResponse(chunks=[b'{"data":[{"id":"portal/default"}]}'])
            elif kind == "timeout":
                raise pixel.httpx.ReadTimeout("private upstream token")
            elif kind == "nested":
                # Below the wire byte cap but beyond Python's parser nesting
                # budget. Diagnostics cannot take down healthy chat status.
                self.response = FakeResponse(chunks=[b"[" * 4000 + b"0" + b"]" * 4000])
            else:
                self.response = FakeResponse(status=404 if kind == "missing" else 200,
                                             chunks=[json.dumps(value).encode()])
            return super().stream(method, url, **kwargs)
    with patch.object(pixel.httpx, "AsyncClient", side_effect=lambda **_kwargs: Client(None)):
        status = await pixel.pixel_status()
    assert status["available"] is True
    assert status["runtimeMatchesRelease"] is (False if kind == "mismatch" else None)
    assert status["runtimeIdentity"] == (value if kind in ("partial", "mismatch") else unknown_runtime_identity())
    assert "ready" not in status["detail"]
    assert "must-not-leak" not in json.dumps(status)
    assert calls[-1][1] == "http://pixel-edge:9595/v1/runtime-identity"
    assert calls[-1][2]["headers"]["Authorization"] == "Bearer " + "e" * 64


@pytest.mark.parametrize("enabled", [True, False])
def test_authenticated_status_never_caches_live_or_unknown_identity(monkeypatch, enabled):
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", "identity-test-owner-key")
    if enabled:
        monkeypatch.setenv("PIXEL_OPENWEBUI_KEY", "e" * 64)
    else:
        monkeypatch.delenv("PIXEL_OPENWEBUI_KEY", raising=False)
    async def host(*_args, **_kwargs):
        return {"status": "idle"}
    monkeypatch.setattr(pixel, "request_agent_json", host)
    app = FastAPI()
    app.include_router(pixel.router)
    with patch.object(pixel.httpx, "AsyncClient", return_value=FakeClient(FakeResponse(chunks=[b'{"data":[{"id":"portal/default"}]}']))):
        result = TestClient(app).get("/api/pixel/status", headers={"Authorization": "Bearer identity-test-owner-key"})
    assert result.status_code == 200
    assert result.headers["Cache-Control"] == "no-store"
    assert result.json()["available"] is enabled
