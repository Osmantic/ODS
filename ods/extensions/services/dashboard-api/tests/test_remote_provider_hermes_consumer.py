"""Remote activation must move the persisted and running Hermes consumer."""

import json

import pytest
import yaml

from test_host_agent import _mod as agent


@pytest.fixture
def hermes_route(tmp_path, monkeypatch):
    install = tmp_path / "ods"
    data = install / "data"
    cloud = install / "config/litellm/cloud.yaml"
    cloud.parent.mkdir(parents=True)
    cloud.write_text("previous-cloud\n")
    live = data / "hermes/config.yaml"
    template = install / "extensions/services/hermes/cli-config.yaml.template"
    live.parent.mkdir(parents=True)
    template.parent.mkdir(parents=True)
    original = {
        "model": {"default": "ods/current", "base_url": "http://model-router:9099/v1",
                  "api_key": "local-test-key", "context_length": 65536},
        "agent": {"max_turns": 17},
    }
    for path in (live, template):
        path.write_text(yaml.safe_dump(original))
        path.chmod(0o600)
    env = install / ".env"
    env.write_text("ODS_MODE=local\nLLM_BACKEND=llama-server\nODS_MODEL_SWITCHBOARD=enabled\n"
                   "LLM_API_URL=http://model-router:9099\nMAX_CONTEXT=65536\n"
                   "HERMES_LLM_BASE_URL=http://model-router:9099/v1\n"
                   "HERMES_LLM_API_KEY=local-test-key\nLITELLM_KEY=gateway-test-key\n")
    env.chmod(0o600)
    monkeypatch.setattr(agent, "INSTALL_DIR", install)
    monkeypatch.setattr(agent, "DATA_DIR", data)
    pixel = {"value": {"model": "local-model", "contextLength": 65536,
                       "maxTokens": 4096, "reasoning": False}}
    restarted = []

    def restart(name, state, *, recreate=False):
        restarted.append((name, recreate))
        return True

    def reconcile(contract, **kwargs):
        pixel["value"] = contract
        return "reconciled"

    monkeypatch.setattr(agent, "_capture_container_state", lambda _: {"exists": True, "running": True})
    monkeypatch.setattr(agent, "_restart_existing_container", restart)
    monkeypatch.setattr(agent, "_restore_container_state", restart)
    monkeypatch.setattr(agent, "_wait_for_container_health", lambda _: None)
    monkeypatch.setattr(agent, "_verify_litellm_route", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "_litellm_route_failure", lambda _: None)
    monkeypatch.setattr(agent, "_render_remote_provider_cloud_config", lambda *a: None)
    monkeypatch.setattr(agent, "_managed_pixel_runtime_contract", lambda: pixel["value"])
    monkeypatch.setattr(agent, "_managed_pixel_thinking_control", lambda: None)
    monkeypatch.setattr(agent, "_reconcile_managed_pixel_contract", reconcile)
    route = agent._plan_remote_provider_lifecycle_operation({
        "action": "configure", "provider": {"transport": "direct",
        "baseUrl": "https://provider.example.test", "model": "remote-model"},
        "secrets": {"apiKey": "provider-test-key"},
    })["route"]
    return {"live": live, "template": template, "route": route, "restarted": restarted,
            "original": original, "env": env, "data": data}


def test_remote_activation_updates_persisted_and_running_hermes(hermes_route):
    fixture = hermes_route
    result = agent._activate_remote_provider_route(fixture["route"])
    assert result["proven"] is True
    live = yaml.safe_load(fixture["live"].read_text())
    assert live["model"]["base_url"] == "http://litellm:4000/v1"
    assert live["model"]["api_key"] == "gateway-test-key"
    assert live["agent"] == fixture["original"]["agent"]
    assert ("ods-hermes", True) in fixture["restarted"]


def test_unchanged_remote_proof_rejects_stale_hermes_route(hermes_route):
    fixture = hermes_route
    agent._activate_remote_provider_route(fixture["route"])
    fixture["live"].write_text(yaml.safe_dump(fixture["original"]))
    runtime = agent._remote_provider_runtime_contract(fixture["route"])
    result = agent._verify_current_remote_provider_consumers(fixture["route"], runtime)
    assert result is None, "An unchanged receipt must not certify Hermes still on the local route"


def test_remote_disable_restores_hermes_config_and_running_consumer(hermes_route):
    fixture = hermes_route
    agent._activate_remote_provider_route(fixture["route"])
    remote = json.loads(json.dumps(fixture["original"]))
    remote["model"].update(base_url="http://litellm:4000/v1", api_key="gateway-test-key")
    fixture["live"].write_text(yaml.safe_dump(remote))
    fixture["restarted"].clear()
    assert agent._deactivate_remote_provider_route()["restored"] is True
    live = yaml.safe_load(fixture["live"].read_text())
    assert live["model"]["base_url"] == "http://model-router:9099/v1"
    assert live["model"]["api_key"] == "local-test-key"
    assert ("ods-hermes", True) in fixture["restarted"]
