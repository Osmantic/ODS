"""The graph recipe must materialize its read-only entrypoint before startup."""

from http.server import HTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import Mock

import yaml

from routers import extensions
from test_host_agent import _mod, host_agent_wire_client  # noqa: F401


def test_neo4j_http_install_syncs_the_mounted_guard(test_client, monkeypatch, tmp_path, host_agent_wire_client):
    library = Path(__file__).resolve().parents[4] / "extensions/library/services"
    users = tmp_path / "data/user-extensions"
    monkeypatch.setattr(extensions, "EXTENSIONS_LIBRARY_DIR", library)
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", users)
    monkeypatch.setattr(extensions, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(extensions, "_call_agent_invalidate_compose_cache", Mock(return_value=True))
    monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", users)
    monkeypatch.setattr(_mod, "EXTENSIONS_DIR", tmp_path / "builtin")
    monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")

    def accept_install(service_id):
        assert service_id == "neo4j"
        installed = users / service_id
        compose = yaml.safe_load((installed / "compose.yaml").read_text())
        mount = next(item for item in compose["services"][service_id]["volumes"]
                     if isinstance(item, dict) and item["target"] == "/opt/ods-neo4j-entrypoint.sh")
        target = tmp_path / mount["source"]
        assert target.is_file(), "Host startup must receive an existing entrypoint file"
        assert target.read_bytes() == (installed / "config/neo4j/entrypoint.sh").read_bytes()
        assert mount["read_only"] is True
        return True

    agent = Mock(side_effect=accept_install)
    monkeypatch.setattr(extensions, "_call_agent_install", agent)
    server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        host_agent_wire_client(server.server_port)
        response = test_client.post("/api/extensions/neo4j/install", headers=test_client.auth_headers)
        assert response.status_code == 200, response.text
        assert response.json()["restart_required"] is False
        agent.assert_called_once_with("neo4j")
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
