"""Template HTTP contract for installed CLI tools that have no health endpoint."""

from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def cli_template(tmp_path, monkeypatch):
    import helpers
    import routers.extensions as extensions
    import routers.templates as templates

    # Use the shipped Aider contract: port=0 and startup_check=false.
    manifest = Path(__file__).resolve().parents[4] / "extensions/library/services/aider/manifest.yaml"
    aider = yaml.safe_load(manifest.read_text())["service"]
    user_dir = tmp_path / "extensions"
    tool_dir = user_dir / "aider"
    tool_dir.mkdir(parents=True)
    (tool_dir / "manifest.yaml").write_text(yaml.safe_dump({"service": aider}))
    (tool_dir / "compose.yaml").write_text(
        "services:\n  aider:\n    image: example/aider:fixture\n    user: '1000:1000'\n"
    )
    monkeypatch.setattr(templates, "TEMPLATES", [
        {"id": "coding", "name": "Coding", "services": ["aider"]},
    ])
    monkeypatch.setattr(templates, "EXTENSION_CATALOG", [aider])
    monkeypatch.setattr(templates, "SERVICES", {})
    monkeypatch.setattr(extensions, "SERVICES", {})
    monkeypatch.setattr(templates, "GPU_BACKEND", "nvidia")
    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", user_dir)
    monkeypatch.setattr(templates, "USER_EXTENSIONS_DIR", user_dir)
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", tmp_path / "builtins")
    monkeypatch.setattr(extensions, "EXTENSIONS_LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(helpers, "get_cached_services", lambda: [])
    monkeypatch.setattr(extensions, "_read_progress", lambda _sid: None)
    monkeypatch.setattr(extensions, "_extensions_lock", nullcontext)
    monkeypatch.setattr(extensions, "_call_agent_invalidate_compose_cache", lambda: None)
    agent = MagicMock(return_value=True)
    hook = MagicMock(return_value=True)
    monkeypatch.setattr(extensions, "_call_agent", agent)
    monkeypatch.setattr(extensions, "_call_agent_hook", hook)
    app = FastAPI()
    app.include_router(templates.router)
    with TestClient(app, headers={"Authorization": "Bearer test-key-12345"}) as client:
        yield client, tool_dir, aider, agent, hook


def test_preview_does_not_offer_to_enable_installed_aider(cli_template):
    client, _tool, _aider, agent, hook = cli_template
    result = client.post("/api/templates/coding/preview")
    assert result.status_code == 200
    assert result.json()["changes"]["to_enable"] == []
    assert result.json()["changes"]["already_enabled"] == ["aider"]
    agent.assert_not_called()
    hook.assert_not_called()


def test_apply_keeps_installed_aider_ready_without_rerunning_it(cli_template):
    client, _tool, _aider, agent, hook = cli_template
    for _ in range(2):
        result = client.post("/api/templates/coding/apply")
        assert result.status_code == 200
        assert result.json()["results"] == {"aider": "already_enabled"}
        assert result.json()["started_count"] == 0
        assert result.json()["enabled_count"] == 0
        assert result.json()["failed_services"] == []
    agent.assert_not_called()
    hook.assert_not_called()


@pytest.mark.parametrize("kind", ["disabled-cli", "stopped-daemon", "errored-cli"])
def test_non_ready_extensions_can_still_be_started(cli_template, monkeypatch, kind):
    client, tool, aider, agent, _hook = cli_template
    if kind == "disabled-cli":
        (tool / "compose.yaml").rename(tool / "compose.yaml.disabled")
    elif kind == "stopped-daemon":
        aider.update(port=8080, startup_check=True)
    else:
        monkeypatch.setattr("routers.extensions._read_progress", lambda _sid: {"status": "error"})
    result = client.post("/api/templates/coding/apply")
    assert result.status_code == 200
    assert result.json()["started_count"] == 1
    agent.assert_called_once_with("start", "aider")
