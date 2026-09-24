"""Authenticated disabled RVC updates must install the real guarded recipe."""

from pathlib import Path
import shutil
from unittest.mock import Mock

import yaml

from routers import extensions
from test_extensions import _patch_mutation_config


def test_disabled_rvc_update_installs_guard_and_rollback_stays_disabled(
    test_client, monkeypatch, tmp_path,
):
    shipped = Path(__file__).resolve().parents[4] / "extensions/library/services/rvc"
    library = tmp_path / "library"
    source = library / "rvc"
    shutil.copytree(shipped, source)
    compose = source / "compose.yaml"
    candidate = compose.read_text()
    old = yaml.safe_load(candidate)
    del old["services"]["rvc"]["command"]
    compose.write_text(yaml.safe_dump(old))
    users = tmp_path / "user"
    _patch_mutation_config(monkeypatch, tmp_path, lib_dir=library, user_dir=users)
    monkeypatch.setattr(extensions, "EXTENSION_CATALOG", [{"id": "rvc", "name": "RVC", "port": 7865}])
    monkeypatch.setattr(extensions, "_call_agent_invalidate_compose_cache", Mock(return_value=None))
    monkeypatch.setattr(extensions, "_sync_extension_config", Mock(return_value=True))
    monkeypatch.setattr(extensions, "_call_agent_hook", Mock(return_value=True))
    runtime = Mock(return_value=True)
    monkeypatch.setattr(extensions, "_call_agent", runtime)
    with extensions._extensions_lock():
        extensions._install_from_library("rvc")
    installed = users / "rvc"
    (installed / "compose.yaml").rename(installed / "compose.yaml.disabled")
    compose.write_text(candidate)

    response = test_client.post("/api/extensions/rvc/update", headers=test_client.auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["action"] == "updated"
    assert not (installed / "compose.yaml").exists()
    assert (installed / "compose.yaml.disabled").read_text() == candidate
    runtime.assert_not_called()

    response = test_client.post("/api/extensions/rvc/rollback", headers=test_client.auth_headers)
    assert response.status_code == 200, response.text
    assert not (installed / "compose.yaml").exists()
    restored = yaml.safe_load((installed / "compose.yaml.disabled").read_text())
    assert "command" not in restored["services"]["rvc"]
    runtime.assert_not_called()
