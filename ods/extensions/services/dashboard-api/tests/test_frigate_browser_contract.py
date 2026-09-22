"""Frigate discovery must retain HTTPS without changing the internal probe."""

from pathlib import Path
import shutil

from config import load_extension_manifests
from user_extensions import scan_user_extension_services

SERVICE = Path(__file__).resolve().parents[3] / "library/services/frigate"


def test_installed_frigate_links_use_tls_and_keep_internal_health(tmp_path, test_client, monkeypatch):
    installed = tmp_path / "frigate"
    shutil.copytree(SERVICE, installed)
    services, _, _ = load_extension_manifests(tmp_path, "nvidia")
    config = services["frigate"]
    assert config["ui_scheme"] == "https"
    assert config["port"] == 5000
    assert config["health"] == "/api/version"
    assert config["external_port"] == 8971
    dynamic = scan_user_extension_services(tmp_path)["frigate"]
    assert dynamic["ui_scheme"] == "https"
    assert dynamic["port"] == 5000
    monkeypatch.setattr("main.SERVICES", services)
    response = test_client.get("/api/external-links", headers=test_client.auth_headers)
    assert response.status_code == 200
    link = next(item for item in response.json() if item["id"] == "frigate")
    assert link["ui_scheme"] == "https"
    assert link["port"] == 8971
    from main import _fallback_services
    fallback = next(item for item in _fallback_services() if item["id"] == "frigate")
    assert fallback["url"] == "https://127.0.0.1:8971/"
    assert fallback["ui_scheme"] == "https"
