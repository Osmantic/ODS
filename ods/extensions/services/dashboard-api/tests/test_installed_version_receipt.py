"""The updater receipt must win in every public installed-version snapshot."""
import json
from unittest.mock import AsyncMock

import pytest

from routers import node, updates


@pytest.fixture
def version_endpoints(tmp_path, monkeypatch, test_client):
    monkeypatch.setattr(node, "_install_root", lambda: tmp_path)
    monkeypatch.setattr(updates, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(node, "get_gpu_info", lambda: None)
    monkeypatch.setattr(node, "get_loaded_model", AsyncMock(return_value=None))
    monkeypatch.setattr(node, "get_all_services", AsyncMock(return_value=[]))
    monkeypatch.setattr(updates, "_get_cached_release_payload", lambda **kwargs: {
        "latest": "2.7.0", "checked_at": "2026-10-23T00:00:00+00:00"})
    (tmp_path / ".env").write_text('ODS_VERSION="2.6.0"\n', encoding="utf-8")
    return tmp_path, test_client


def assert_versions(client, expected):
    version = client.get("/api/version", headers=client.auth_headers)
    node_info = client.get("/api/node/capabilities", headers=client.auth_headers)
    assert version.status_code == node_info.status_code == 200
    assert version.json()["current"] == expected
    assert node_info.json()["ods_version"] == expected
    return version.json()


@pytest.mark.parametrize("receipt", [
    json.dumps({"version": "2.7.0", "last_update": "2026-10-23T00:00:00Z"}),
    "2.7.0\n",
])
@pytest.mark.parametrize("endpoint,field", [("/api/version", "current"), ("/api/node/capabilities", "ods_version")])
def test_successful_update_is_visible_without_rewriting_install_env(version_endpoints, receipt, endpoint, field):
    root, client = version_endpoints
    # Before the successful update, the install-time environment is still valid.
    assert_versions(client, "2.6.0")
    (root / ".version").write_text(receipt, encoding="utf-8")
    response = client.get(endpoint, headers=client.auth_headers)
    assert response.status_code == 200
    assert response.json()[field] == "2.7.0"
    if endpoint == "/api/version":
        assert response.json()["update_available"] is False
    assert (root / ".env").read_text(encoding="utf-8") == 'ODS_VERSION="2.6.0"\n'


@pytest.mark.parametrize("receipt", [None, "", "{broken", "{}", '{"version": null}'])
def test_absent_or_incomplete_receipt_retains_install_env(version_endpoints, receipt):
    root, client = version_endpoints
    if receipt is not None:
        (root / ".version").write_text(receipt, encoding="utf-8")
    assert_versions(client, "2.6.0")


def test_unreadable_receipt_retains_install_env(version_endpoints, monkeypatch):
    root, client = version_endpoints
    receipt = root / ".version"
    receipt.write_text('{"version": "2.7.0"}', encoding="utf-8")
    original = type(receipt).read_text

    def read_text(path, *args, **kwargs):
        if path == receipt:
            raise PermissionError("isolated unreadable receipt")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(receipt), "read_text", read_text)
    assert_versions(client, "2.6.0")
