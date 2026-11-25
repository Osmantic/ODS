"""API responses report a fixed error category; the exception text goes to the log.

Each case injects an error whose text contains SENTINEL and checks that the
response names the failure without repeating that text (CodeQL
py/stack-trace-exposure).
"""

from __future__ import annotations

import asyncio
import email.message
import io
import urllib.error
from pathlib import Path
from unittest.mock import patch

import aiohttp
import httpx

SENTINEL = "sentinel-detail-7f3a"
_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "config" / "model-state.schema.v1.json"


def _model_state_path(monkeypatch, tmp_path, schema_path=_SCHEMA_PATH):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("ODS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ODS_MODEL_STATE_SCHEMA_PATH", str(schema_path))
    return data_dir / "model-state.json"


def test_model_state_reports_invalid_json_without_parser_text(test_client, monkeypatch, tmp_path):
    _model_state_path(monkeypatch, tmp_path).write_text("{not json", encoding="utf-8")

    resp = test_client.get("/api/models/state", headers=test_client.auth_headers)

    assert resp.status_code == 200
    assert resp.json()["errors"] == ["not valid JSON"]
    assert "Expecting" not in resp.text


def test_model_state_reports_missing_schema_without_its_path(test_client, monkeypatch, tmp_path):
    missing = tmp_path / f"{SENTINEL}-schema.json"
    _model_state_path(monkeypatch, tmp_path, schema_path=missing).write_text("{}", encoding="utf-8")

    resp = test_client.get("/api/models/state", headers=test_client.auth_headers)

    assert resp.json()["errors"] == ["state schema unavailable or invalid"]
    assert SENTINEL not in resp.text


def test_oauth_pending_reports_unreadable_callback_without_parser_text(test_client, monkeypatch, tmp_path):
    monkeypatch.setenv("ODS_PERSONA_DIR", str(tmp_path))
    (tmp_path / "oauth_callback.json").write_text("{", encoding="utf-8")

    resp = test_client.get("/api/oauth/pending", headers=test_client.auth_headers)

    assert resp.json() == {"pending": False, "error": "could not read callback file"}


def test_oauth_providers_reports_unreadable_registry_without_its_path(test_client, monkeypatch, tmp_path):
    registry = tmp_path / f"{SENTINEL}-providers.json"
    registry.write_text("{", encoding="utf-8")
    monkeypatch.setenv("ODS_OAUTH_PROVIDERS_FILE", str(registry))

    resp = test_client.get("/api/oauth/providers", headers=test_client.auth_headers)

    body = resp.json()
    assert body["registry_available"] is False
    assert body["error"] == "provider registry unreadable"
    assert SENTINEL not in resp.text and "Expecting" not in resp.text


def test_remote_provider_status_reports_invalid_state_without_parser_text(test_client, monkeypatch, tmp_path):
    from routers import remote_provider_status as rps

    state_path = tmp_path / "routing-state.json"
    state_path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(rps, "_state_path", lambda: state_path)
    monkeypatch.setattr(rps, "_activation_path", lambda: tmp_path / "activation-public.json")

    async def verified_activation(value):
        return value

    async def egress_health():
        return {"reachable": True, "valid": True, "ready": False, "status": "disabled",
                "reason": "remote_route_disabled", "secret": {"configured": False, "bytes": 0},
                "resolution": None}

    monkeypatch.setattr(rps, "_reconcile_activation_with_host", verified_activation)
    monkeypatch.setattr(rps, "_fetch_egress_health", egress_health)

    resp = test_client.get("/api/remote-provider/status", headers=test_client.auth_headers)

    assert resp.json()["routeState"]["errors"] == ["not valid JSON"]
    assert "Expecting" not in resp.text


def _setup_stream(test_client):
    with test_client.stream("POST", "/api/setup/test", headers=test_client.auth_headers) as response:
        assert response.status_code == 200
        return "".join(response.iter_text())


def test_setup_connectivity_fallback_reports_unreachable_without_error_text(test_client, monkeypatch, tmp_path):
    install_root = tmp_path / "ods"
    install_root.mkdir()
    monkeypatch.setattr("routers.setup.INSTALL_DIR", str(install_root))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("routers.setup.SERVICES", {
        "demo": {"name": "Demo", "host": "demo", "port": 8080, "health": "/health"},
    })

    def refuse(_session, _url, **_kwargs):
        raise aiohttp.ClientConnectionError(SENTINEL)

    monkeypatch.setattr(aiohttp.ClientSession, "get", refuse)

    text = _setup_stream(test_client)

    assert "✗ Demo: unreachable" in text
    assert SENTINEL not in text
    assert text.rstrip().endswith("__ODS_RESULT__:FAIL:1")


def test_setup_runner_error_points_to_logs_without_error_text(test_client, monkeypatch, tmp_path):
    install_root = tmp_path / "ods"
    (install_root / "scripts").mkdir(parents=True)
    (install_root / "scripts" / "ods-test-functional.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    monkeypatch.setattr("routers.setup.INSTALL_DIR", str(install_root))

    class FailingOutput:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError(SENTINEL)

    class Process:
        pid = None
        returncode = 1
        stdout = FailingOutput()

        def kill(self):
            pass

        async def wait(self):
            return 1

    async def create_subprocess_exec(program, _script_path, **_kwargs):
        assert program == "bash"
        return Process()

    # The runner kills the diagnostic's process group when it stops early.
    killed = []
    monkeypatch.setattr("routers.setup.asyncio.create_subprocess_exec", create_subprocess_exec)
    monkeypatch.setattr("routers.setup.os.killpg", lambda pid, sig: killed.append(pid), raising=False)

    text = _setup_stream(test_client)

    assert "Diagnostic runner error (see dashboard-api logs)" in text
    assert SENTINEL not in text
    assert text.rstrip().endswith("__ODS_RESULT__:FAIL:1")


def test_update_dry_run_reports_github_failure_without_error_text(test_client, monkeypatch, tmp_path):
    import routers.updates as updates_mod

    install_dir = tmp_path / "ods"
    install_dir.mkdir()
    (install_dir / ".env").write_text("ODS_VERSION=1.3.0\n", encoding="utf-8")
    monkeypatch.setattr(updates_mod, "INSTALL_DIR", str(install_dir))

    with patch("routers.updates.httpx.AsyncClient.get", side_effect=httpx.ConnectError(SENTINEL)):
        resp = test_client.get("/api/update/dry-run", headers=test_client.auth_headers)

    assert resp.json()["version_check_error"] == "Could not reach GitHub"
    assert SENTINEL not in resp.text


def test_manifest_errors_name_the_extension_without_exception_text(monkeypatch):
    import main

    monkeypatch.setattr(main, "MANIFEST_ERRORS", [
        {"file": "/ods/extensions/services/demo/manifest.yaml", "error": f"while parsing {SENTINEL}"},
        {"file": "/ods/extensions/services/legacy/manifest.yaml", "error": "Unsupported schema_version"},
    ])

    assert main._public_manifest_errors() == [
        {"file": "demo", "error": "manifest could not be loaded"},
        {"file": "legacy", "error": "Unsupported schema_version"},
    ]


def test_usage_report_names_token_spy_failures_without_error_text(monkeypatch):
    from routers import usage

    monkeypatch.setattr(usage, "TOKEN_SPY_URL", "http://token-spy:8080")

    def unreachable(*_args):
        raise urllib.error.URLError(SENTINEL)

    monkeypatch.setattr(usage, "_request_token_spy_report", unreachable)
    report = asyncio.run(usage._fetch_token_spy_report("2026-10-01", "2026-10-02"))
    assert report["source"]["detail"] == "Token Spy unavailable"

    def rejected(*_args):
        raise urllib.error.HTTPError("http://token-spy:8080/api/report", 503, "unavailable", email.message.Message(),
                                     io.BytesIO(SENTINEL.encode()))

    monkeypatch.setattr(usage, "_request_token_spy_report", rejected)
    report = asyncio.run(usage._fetch_token_spy_report("2026-10-01", "2026-10-02"))
    assert report["source"]["detail"] == "Token Spy returned HTTP 503"
    assert SENTINEL not in str(report)
