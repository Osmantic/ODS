"""The authenticated install must materialize the flow server definition before host startup."""

from http.server import HTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import Mock

import yaml

from routers import extensions
from test_host_agent import _mod, host_agent_wire_client  # noqa: F401


def test_prefect_http_install_preserves_authentication_and_prepares_nonroot_storage(
    test_client, monkeypatch, tmp_path, host_agent_wire_client,
):
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
    monkeypatch.setattr(_mod.os, "getuid", lambda: 0)
    ownership = Mock()
    monkeypatch.setattr(_mod.os, "chown", ownership)
    monkeypatch.setattr(_mod, "_fs_type", lambda _path: "ext4")

    def accept_install(service_id):
        assert service_id == "prefect"
        installed = users / service_id
        service = yaml.safe_load((installed / "compose.yaml").read_text())["services"][service_id]
        assert service["command"] == yaml.safe_load((library / "prefect/compose.yaml").read_text())["services"]["prefect"]["command"]
        assert service["environment"]["PREFECT_SERVER_API_CSRF_PROTECTION_ENABLED"] == "true"
        assert service["user"] == "1000:1000"
        _mod._precreate_data_dirs(service_id)
        assert (tmp_path / "data/prefect").is_dir()
        ownership.assert_any_call(str(tmp_path / "data/prefect"), 1000, 1000)
        return True

    install = Mock(side_effect=accept_install)
    monkeypatch.setattr(extensions, "_call_agent_install", install)
    server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        host_agent_wire_client(server.server_port)
        response = test_client.post("/api/extensions/prefect/install", headers=test_client.auth_headers)
        assert response.status_code == 200, response.text
        assert response.json()["restart_required"] is False
        install.assert_called_once_with("prefect")
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
