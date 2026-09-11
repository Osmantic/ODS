"""Purge must not follow an extension's data alias into another directory."""
import os

import pytest


@pytest.mark.skipif(os.name == "nt", reason="Directory symlink creation requires Windows privileges")
def test_purge_refuses_sibling_data_alias(test_client, monkeypatch, tmp_path):
    import routers.extensions as extensions

    monkeypatch.setattr(extensions, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", tmp_path / "builtin")
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")
    sibling = tmp_path / "another-service"
    sibling.mkdir()
    evidence = sibling / "keep.db"
    evidence.write_text("other service data")
    alias = tmp_path / "my-ext"
    alias.symlink_to(sibling, target_is_directory=True)

    response = test_client.request(
        "DELETE", "/api/extensions/my-ext/data",
        headers=test_client.auth_headers, json={"confirm": True},
    )

    assert response.status_code == 400
    assert "redirect" in response.json()["detail"].lower()
    assert evidence.read_text() == "other service data"
    assert alias.is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="Directory symlink creation requires Windows privileges")
def test_purge_supports_a_configured_data_root_alias(test_client, monkeypatch, tmp_path):
    import routers.extensions as extensions

    root = tmp_path / "actual-data"
    root.mkdir()
    configured = tmp_path / "configured-data"
    configured.symlink_to(root, target_is_directory=True)
    target = root / "my-ext"
    target.mkdir()
    (target / "old.db").write_text("purge this")
    monkeypatch.setattr(extensions, "DATA_DIR", str(configured))
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", tmp_path / "builtin")
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")

    response = test_client.request(
        "DELETE", "/api/extensions/my-ext/data",
        headers=test_client.auth_headers, json={"confirm": True},
    )

    assert response.status_code == 200
    assert response.json()["action"] == "purged"
    assert not target.exists()
    assert configured.is_symlink()
