"""Dependent services must not start after a prerequisite fails to start."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from routers import extensions


@pytest.fixture
def installation(monkeypatch, tmp_path):
    bundled = tmp_path / "bundled"
    source = Path(__file__).resolve().parents[2]
    for name in ("hermes-proxy", "hermes", "searxng"):
        directory = bundled / name
        directory.mkdir(parents=True)
        (directory / "manifest.yaml").write_bytes((source / name / "manifest.yaml").read_bytes())
        (directory / "compose.yaml.disabled").write_text(
            f"services:\n  {name}:\n    image: alpine:3.22\n")

    def rename(action, name):
        assert action == "activate"
        directory = bundled / name
        (directory / "compose.yaml.disabled").rename(directory / "compose.yaml")
        return True

    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", bundled)
    monkeypatch.setattr(extensions, "DATA_DIR", str(tmp_path))
    start, hook = Mock(return_value=True), Mock(return_value=True)
    monkeypatch.setattr(extensions, "_call_agent", start)
    monkeypatch.setattr(extensions, "_call_agent_hook", hook)
    monkeypatch.setattr(extensions, "_call_agent_compose_rename", rename)
    monkeypatch.setattr(extensions, "_call_agent_invalidate_compose_cache", Mock())
    return tmp_path, start, hook


@pytest.mark.parametrize("failure", ["pre_start", "start"])
@pytest.mark.parametrize("enabled_target", [False, True])
def test_failed_dependency_blocks_transitive_start(test_client, installation, failure, enabled_target):
    root, start, hook = installation
    if enabled_target:
        target = root / "bundled/hermes-proxy"
        (target / "compose.yaml.disabled").rename(target / "compose.yaml")
    if failure == "pre_start":
        hook.side_effect = lambda service, phase: (service, phase) != ("searxng", "pre_start")
    else:
        start.side_effect = lambda action, service: service != "searxng"

    response = test_client.post("/api/extensions/hermes-proxy/enable?auto_enable_deps=true",
                                headers=test_client.auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["restart_required"] is True
    expected_calls = [] if failure == "pre_start" else [("start", "searxng")]
    assert [call.args for call in start.call_args_list] == expected_calls
    assert body["failed_services"] == ["searxng", "hermes", "hermes-proxy"]
    for service in body["failed_services"]:
        progress = json.loads((root / "extension-progress" / f"{service}.json").read_text())
        assert progress["status"] == "error"
    assert any("hermes" in warning and "searxng" in warning for warning in body["warnings"])
    assert any("hermes-proxy" in warning and "hermes" in warning for warning in body["warnings"])
    assert not any(call.args[0] in {"hermes", "hermes-proxy"} for call in hook.call_args_list)


def test_nonterminal_post_start_warning_does_not_block_dependents(test_client, installation):
    _, start, hook = installation
    hook.side_effect = lambda service, phase: (service, phase) != ("searxng", "post_start")

    response = test_client.post("/api/extensions/hermes-proxy/enable?auto_enable_deps=true",
                                headers=test_client.auth_headers)

    assert response.status_code == 200
    assert response.json()["restart_required"] is False
    assert response.json()["warnings"]
    assert [call.args for call in start.call_args_list] == [
        ("start", "searxng"), ("start", "hermes"), ("start", "hermes-proxy"),
    ]


def test_failure_is_found_through_an_already_enabled_intermediate(test_client, installation):
    root, start, _ = installation
    bundled = root / "bundled"
    (bundled / "hermes" / "compose.yaml.disabled").rename(bundled / "hermes" / "compose.yaml")
    manifest = bundled / "hermes-proxy" / "manifest.yaml"
    content = yaml.safe_load(manifest.read_text())
    content["service"]["depends_on"].append("searxng")
    manifest.write_text(yaml.safe_dump(content))
    start.side_effect = lambda action, service: service != "searxng"

    response = test_client.post("/api/extensions/hermes-proxy/enable?auto_enable_deps=true",
                                headers=test_client.auth_headers)

    assert response.status_code == 200
    assert response.json()["failed_services"] == ["searxng", "hermes-proxy"]
    start.assert_called_once_with("start", "searxng")
    assert not (root / "extension-progress" / "hermes.json").exists()


@pytest.mark.parametrize("start_ok", [False, True])
def test_enabled_target_reports_its_start_outcome(test_client, installation, start_ok):
    root, start, _ = installation
    for service in ("hermes-proxy", "hermes", "searxng"):
        directory = root / "bundled" / service
        (directory / "compose.yaml.disabled").rename(directory / "compose.yaml")
    start.return_value = start_ok

    response = test_client.post("/api/extensions/hermes-proxy/enable",
                                headers=test_client.auth_headers)

    assert response.status_code == 200
    assert response.json()["failed_services"] == ([] if start_ok else ["hermes-proxy"])
    assert response.json()["restart_required"] is not start_ok
    start.assert_called_once_with("start", "hermes-proxy")
    if not start_ok:
        progress = json.loads((root / "extension-progress/hermes-proxy.json").read_text())
        assert progress["status"] == "error"
        assert "ods restart" in progress["error"]
