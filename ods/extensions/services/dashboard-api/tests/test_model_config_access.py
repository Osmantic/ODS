"""Model controls use a fresh, public host snapshot only for denied file access."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def model_config_api(monkeypatch, tmp_path):
    import routers.models as router

    install = tmp_path / "ods"
    install.mkdir()
    env_file = install / ".env"
    env_file.write_text("ODS_MODE=local\n", encoding="utf-8")
    monkeypatch.setattr(router, "INSTALL_DIR", str(install))
    monkeypatch.setattr(router, "_ENV_PATH", env_file)
    monkeypatch.setattr(router, "ODS_MODE_EFFECTIVE", "local")
    monkeypatch.setattr(router, "LLM_BACKEND", "llama-server")
    monkeypatch.setenv("ODS_MODE", "local")

    # Exercise the HTTP routes without contacting any model runtime or Docker.
    monkeypatch.setattr(router, "get_gpu_info", lambda: None)
    monkeypatch.setattr(router, "get_loaded_model", AsyncMock(return_value=None))
    monkeypatch.setattr(router, "_fetch_llama_loaded_model", AsyncMock(return_value=None))
    monkeypatch.setattr(router, "get_llama_metrics", AsyncMock(return_value={}))
    monkeypatch.setattr(router, "get_llama_context_size", AsyncMock(return_value=None))
    monkeypatch.setattr(router, "_verified_activation_context", lambda *_: None)
    monkeypatch.setattr(router, "_get_agent_model_status", lambda: None)
    monkeypatch.setattr(router, "_installed_model_paths", lambda: {})
    monkeypatch.setattr(router, "_load_library", lambda: [])
    monkeypatch.setattr(router, "build_models_payload", lambda *_args, **_kwargs: {
        "models": [], "gpu": None, "currentModel": None,
    })
    monkeypatch.setattr(router, "_windows_hosted_runtime", lambda: False)
    monkeypatch.setattr(router, "_find_loadable_model", lambda *_: {"id": "target"})
    monkeypatch.setattr(router, "_already_active_model", lambda *_: (False, None))
    monkeypatch.setattr(router, "_configured_model_identity_matches", lambda *_: False)
    monkeypatch.setattr(router, "_recommended_model_context", lambda *_: None)
    monkeypatch.setattr(router, "_policy_activation_context", lambda *_: None)
    monkeypatch.setattr(router, "pixel_stream_active", lambda: False)
    monkeypatch.setattr(router, "_bootstrap_upgrade_download_conflict", lambda: None)

    state: dict[str, Any] = {
        "projection": {"configuredMode": "local"},
        "host_error": None,
        "read_error": None,
        "host_calls": [],
        "activation_calls": [],
    }
    original_read = Path.read_text

    def read_text(path, *args, **kwargs):
        if path == env_file and state["read_error"] is not None:
            raise state["read_error"]
        return original_read(path, *args, **kwargs)

    def host_config(method, path, *, timeout, **kwargs):
        state["host_calls"].append((method, path, timeout, kwargs))
        if state["host_error"] is not None:
            raise state["host_error"]
        return state["projection"]

    def activate(path, body, **kwargs):
        state["activation_calls"].append((path, body, kwargs))
        return {"status": "loaded", "model_id": body["model_id"]}

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(router, "request_agent_json", host_config)
    monkeypatch.setattr(router, "_call_agent_model", activate)
    state.update(router=router, env_file=env_file)
    return state


def _get(client):
    response = client.get("/api/models", headers=client.auth_headers)
    assert response.status_code == 200
    return response.json()


def _load(client):
    return client.post("/api/models/target/load", headers=client.auth_headers)


def _assert_fresh_host_calls(state, count=2):
    assert state["host_calls"] == [("GET", "/v1/model/config", 5, {})] * count


def test_permission_denied_env_uses_host_config_for_listing_and_activation(test_client, model_config_api):
    state = model_config_api
    state["read_error"] = PermissionError("private configuration belongs to another UID")

    payload = _get(test_client)
    assert (payload["odsMode"], payload["configuredMode"]) == ("local", "local")
    response = _load(test_client)

    assert response.status_code == 200
    assert response.json()["status"] == "loaded"
    assert len(state["activation_calls"]) == 1
    _assert_fresh_host_calls(state)


def test_permission_denied_file_and_cloud_host_snapshot_preserve_mode_mismatch(test_client, model_config_api):
    state = model_config_api
    state["read_error"] = PermissionError("private configuration")
    state["projection"] = {"configuredMode": "cloud"}

    payload = _get(test_client)
    assert (payload["odsMode"], payload["configuredMode"]) == ("local", "cloud")
    response = _load(test_client)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ods_mode_mismatch"
    assert state["activation_calls"] == []
    _assert_fresh_host_calls(state)


def test_activation_reads_host_configuration_again_after_listing(test_client, model_config_api):
    state = model_config_api
    state["read_error"] = PermissionError("private configuration")
    assert _get(test_client)["configuredMode"] == "local"

    state["projection"] = {"configuredMode": "cloud"}
    response = _load(test_client)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ods_mode_mismatch"
    assert state["activation_calls"] == []
    _assert_fresh_host_calls(state)


@pytest.mark.parametrize("projection", [
    None, [], "local", {}, {"ODS_MODE": "local"},
    {"configuredMode": None}, {"configuredMode": True},
    {"configuredMode": {"mode": "local"}}, {"configuredMode": []},
    {"configuredMode": ""}, {"configuredMode": "unknown"},
    {"configuredMode": "unsupported"},
])
def test_invalid_host_config_never_trusts_process_local_mode(test_client, model_config_api, projection):
    state = model_config_api
    state["read_error"] = PermissionError("private configuration")
    state["projection"] = projection

    payload = _get(test_client)
    assert payload["odsMode"] == "local"
    assert payload["configuredMode"] == "unknown"
    response = _load(test_client)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ods_mode_unknown"
    assert state["activation_calls"] == []
    _assert_fresh_host_calls(state)


@pytest.mark.parametrize("error_type", ["AgentUnavailable", "AgentProtocolError", "AgentHTTPError"])
def test_host_config_failure_blocks_without_process_mode_fallback(test_client, model_config_api, error_type):
    from host_agent_client import AgentHTTPError, AgentProtocolError, AgentUnavailable

    state = model_config_api
    state["read_error"] = PermissionError("private configuration")
    state["host_error"] = {
        "AgentUnavailable": AgentUnavailable("host unavailable"),
        "AgentProtocolError": AgentProtocolError("invalid host response"),
        "AgentHTTPError": AgentHTTPError(401, "host authentication failed"),
    }[error_type]

    assert _get(test_client)["configuredMode"] == "unknown"
    response = _load(test_client)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ods_mode_unknown"
    assert state["activation_calls"] == []
    _assert_fresh_host_calls(state)


@pytest.mark.parametrize("mode", ["local", "hybrid", "cloud"])
def test_readable_config_is_authoritative_without_host_request(test_client, model_config_api, mode):
    state = model_config_api
    state["env_file"].write_text(f"ODS_MODE={mode}\n", encoding="utf-8")
    state["router"].ODS_MODE_EFFECTIVE = mode
    state["projection"] = {"configuredMode": "unsupported"}

    assert _get(test_client)["configuredMode"] == mode
    response = _load(test_client)

    assert response.status_code == (409 if mode == "cloud" else 200)
    assert state["host_calls"] == []


@pytest.mark.parametrize("contents", [None, "", "OTHER=value\n", "ODS_MODE=unsupported\n"])
def test_missing_or_invalid_config_never_queries_host_or_uses_startup_mode(test_client, model_config_api, contents):
    state = model_config_api
    if contents is None:
        state["env_file"].unlink()
    else:
        state["env_file"].write_text(contents, encoding="utf-8")

    assert _get(test_client)["configuredMode"] == "unknown"
    response = _load(test_client)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ods_mode_unknown"
    assert state["host_calls"] == []
    assert state["activation_calls"] == []


def test_other_file_read_error_does_not_query_host(test_client, model_config_api):
    state = model_config_api
    state["read_error"] = OSError("filesystem read failed")

    assert _get(test_client)["configuredMode"] == "unknown"
    response = _load(test_client)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ods_mode_unknown"
    assert state["host_calls"] == []
    assert state["activation_calls"] == []
