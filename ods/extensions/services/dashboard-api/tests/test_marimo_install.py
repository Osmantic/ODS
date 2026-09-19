"""The notebook recipe must survive the real install and re-enable boundaries."""

from pathlib import Path
from unittest.mock import Mock

import yaml

from routers import extensions


def test_marimo_install_and_reenable_use_an_image_only_compose(test_client, monkeypatch, tmp_path):
    library = Path(__file__).resolve().parents[4] / "extensions/library/services"
    users = tmp_path / "user-extensions"
    monkeypatch.setattr(extensions, "EXTENSIONS_LIBRARY_DIR", library)
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", users)
    monkeypatch.setattr(extensions, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(extensions, "_sync_extension_config", Mock(return_value=True))
    monkeypatch.setattr(extensions, "_call_agent_invalidate_compose_cache", Mock(return_value=True))

    def accept_install(service_id):
        assert service_id == "marimo"
        installed = users / service_id
        compose = yaml.safe_load((installed / "compose.yaml").read_text())
        service = compose["services"][service_id]
        assert service["image"] == "ods-marimo:0.24.2-r1"
        assert service["pull_policy"] == "never"
        assert "build" not in service
        manifest = yaml.safe_load((installed / "manifest.yaml").read_text())
        assert manifest["service"]["setup_hook"] == "setup.sh"
        assert (installed / "setup.sh").is_file()
        assert (installed / "Dockerfile").is_file()
        assert (installed / "entrypoint.py").is_file()
        return True

    agent = Mock(side_effect=accept_install)
    monkeypatch.setattr(extensions, "_call_agent_install", agent)
    response = test_client.post("/api/extensions/marimo/install", headers=test_client.auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["restart_required"] is False
    agent.assert_called_once_with("marimo")
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", tmp_path / "builtin")
    monkeypatch.setattr(extensions, "_call_agent", Mock(return_value=True))
    monkeypatch.setattr(extensions, "_call_agent_hook", Mock(return_value=True))
    for action in ("disable?include_data_info=false", "enable"):
        response = test_client.post(f"/api/extensions/marimo/{action}", headers=test_client.auth_headers)
        assert response.status_code == 200, response.text
        assert response.json()["restart_required"] is False
