"""Tests for /api/status environment custody diagnostics (Issue #7961).

Verifies:
1. /api/status exposes envFileReadError when .env cannot be read due to permission error.
2. /api/status omits envFileReadError when .env is readable or after permission recovery.
3. /api/status omits envFileReadError when .env is genuinely absent.
4. No secret values from .env are leaked into the /api/status response.
"""

import json
from pathlib import Path
from unittest.mock import patch

import config


def test_api_status_surfaces_env_file_read_error(test_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    config._reset_env_permission_state()
    secret_token = "sk-super-secret-fixture-key-999"

    def mock_permission_denied(*args, **kwargs):
        raise PermissionError("Simulated EACCES on .env")

    with patch.object(Path, "read_text", mock_permission_denied):
        config._find_env_file_value("LLM_MODEL")
        resp = test_client.get("/api/status", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "envFileReadError" in data
        assert "Permission denied" in data["envFileReadError"]
        assert secret_token not in json.dumps(data)


def test_api_status_clears_env_file_read_error_on_recovery(
    test_client, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    config._reset_env_permission_state()

    # 1. Trigger error
    with patch.object(
        Path, "read_text", side_effect=PermissionError("Simulated denial")
    ):
        config._find_env_file_value("KEY")
        assert config.get_env_file_read_error() is not None

    # 2. Recover with valid file
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_MODEL=valid-model\n", encoding="utf-8")
    found, val = config._find_env_file_value("LLM_MODEL")
    assert found is True
    assert val == "valid-model"
    assert config.get_env_file_read_error() is None

    # 3. /api/status does not have envFileReadError
    resp = test_client.get("/api/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json().get("envFileReadError") is None


def test_api_status_omits_env_file_read_error_when_missing(
    test_client, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    config._reset_env_permission_state()
    missing_env = tmp_path / ".env"
    if missing_env.exists():
        missing_env.unlink()

    found, val = config._find_env_file_value("ANY_KEY")
    assert found is False
    assert val == ""
    assert config.get_env_file_read_error() is None

    resp = test_client.get("/api/status", headers=test_client.auth_headers)
    assert resp.status_code == 200
    assert resp.json().get("envFileReadError") is None
