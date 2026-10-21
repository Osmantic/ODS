"""Template installs preserve host settings through the real config-sync route."""

import importlib.util
import sys
import threading
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.mark.parametrize("layout", ["fresh", "removed", "failed-start", "missing"])
def test_chat_playground_preserves_owner_config(tmp_path, monkeypatch, layout):
    from routers import extensions, templates
    import helpers
    import host_agent_client
    import security

    ods = Path(__file__).resolve().parents[4]
    library = ods / "extensions" / "library" / "services"
    shipped_template = yaml.safe_load(
        (ods / "templates" / "chat-playground.yaml").read_text(encoding="utf-8")
    )["template"]
    spec = importlib.util.spec_from_file_location(
        "template_config_host_agent", ods / "bin" / "ods-host-agent.py"
    )
    agent = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, agent)
    spec.loader.exec_module(agent)

    install = tmp_path / "install"
    user_root = install / "data" / "user-extensions"
    user_root.mkdir(parents=True)
    data = install / "data"
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    # Cached healthy peers must have a currently enabled selected definition.
    for sid in shipped_template["services"]:
        if sid == "sillytavern":
            continue
        peer = builtin / sid
        peer.mkdir()
        (peer / "manifest.yaml").write_text(
            yaml.safe_dump({"service": {"id": sid, "depends_on": []}}), encoding="utf-8"
        )
        (peer / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    target = install / "config" / "sillytavern" / "config.yaml"
    owner_config = "securityOverride: false\nwhitelistMode: true\nwhitelist: ['127.0.0.1']\n"
    if layout != "missing":
        target.parent.mkdir(parents=True)
        target.write_text(owner_config, encoding="utf-8")

    for module in (extensions, agent):
        monkeypatch.setattr(module, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(module, "EXTENSIONS_DIR", builtin)
    monkeypatch.setattr(extensions, "EXTENSIONS_LIBRARY_DIR", library)
    monkeypatch.setattr(extensions, "DATA_DIR", str(data))
    monkeypatch.setattr(templates, "USER_EXTENSIONS_DIR", user_root)
    monkeypatch.setattr(templates, "TEMPLATES", [shipped_template])
    monkeypatch.setattr(agent, "INSTALL_DIR", install)
    monkeypatch.setattr(agent, "AGENT_API_KEY", "template-wire-key")
    monkeypatch.setattr(helpers, "get_cached_services", lambda: [
        SimpleNamespace(id=sid, status="healthy")
        for sid in shipped_template["services"] if sid != "sillytavern"
    ])
    lifecycle: list[tuple[str, str]] = []

    def call_agent(action: str, sid: str) -> bool:
        lifecycle.append((action, sid))
        return True

    monkeypatch.setattr(extensions, "_call_agent", call_agent)
    monkeypatch.setattr(extensions, "_call_agent_hook", lambda sid, hook: True)
    monkeypatch.setattr(extensions, "_call_agent_invalidate_compose_cache", lambda: None)
    monkeypatch.setattr(extensions, "_select_extensions_on_host", lambda *_a, **_kw: None)

    if layout == "removed":
        extensions._install_from_library("sillytavern")
        directory = user_root / "sillytavern"
        (directory / "compose.yaml").rename(directory / "compose.yaml.disabled")
        app = FastAPI()
        app.include_router(extensions.router)
        with TestClient(app) as client:
            response = client.delete(
                "/api/extensions/sillytavern",
                headers={"Authorization": f"Bearer {security.DASHBOARD_API_KEY}"},
            )
        assert response.status_code == 200, response.text
        assert not directory.exists()
        assert target.read_text(encoding="utf-8") == owner_config

    # Only config synchronization reaches a real host-agent HTTP server.
    # Docker lifecycle work remains isolated; its start order is asserted below.
    server = HTTPServer(("127.0.0.1", 0), agent.AgentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(host_agent_client, "AGENT_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(host_agent_client, "_headers", lambda: {"Authorization": "Bearer template-wire-key"})
    existing_client = host_agent_client._sync_client
    monkeypatch.setattr(host_agent_client, "_sync_client", None)
    app = FastAPI()
    app.include_router(templates.router)
    app.include_router(extensions.router)
    try:
        with TestClient(app) as client:
            if layout == "failed-start":
                monkeypatch.setattr(extensions, "_call_agent_install", lambda sid: False)
                failed_install = client.post(
                    "/api/extensions/sillytavern/install",
                    headers={"Authorization": f"Bearer {security.DASHBOARD_API_KEY}"},
                )
                assert failed_install.status_code == 200, failed_install.text
                assert failed_install.json()["restart_required"] is True
                assert extensions._has_error_progress("sillytavern")
                assert target.read_text(encoding="utf-8") == owner_config
            assert client.post("/api/templates/chat-playground/apply").status_code == 401
            response = client.post(
                "/api/templates/chat-playground/apply",
                headers={"Authorization": f"Bearer {security.DASHBOARD_API_KEY}"},
            )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["results"]["sillytavern"] == "library_installed", result
        assert result["failed_services"] == []
        assert result["started_count"] == 1
        assert lifecycle == [("start", "sillytavern")]
        expected = (
            (library / "sillytavern" / "config" / "sillytavern" / "config.yaml").read_text(encoding="utf-8")
            if layout == "missing" else owner_config
        )
        assert target.read_text(encoding="utf-8") == expected
    finally:
        current_client = host_agent_client._sync_client
        if current_client is not None:
            current_client.close()
        host_agent_client._sync_client = existing_client
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
