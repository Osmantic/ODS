"""Hermes Library add-back must follow the selected ODS model route."""

import importlib.util
import json
import os
import stat
import subprocess
from pathlib import Path

import yaml


ODS_ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "ods_host_agent_hermes_addback", ODS_ROOT / "bin" / "ods-host-agent.py",
)
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


def write_install(tmp_path, env, *, live=None):
    template = tmp_path / "extensions/services/hermes/cli-config.yaml.template"
    template.parent.mkdir(parents=True)
    template.write_text(
        'model:\n  default: "template-model"\n  provider: "custom"\n'
        '  base_url: "http://llama-server:8080/v1"\n'
        '  context_length: 131072\nother:\n  owner_value: retained\n',
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("\n".join(f"{k}={json.dumps(v)}" for k, v in env.items()) + "\n")
    live_path = tmp_path / "data/hermes/config.yaml"
    if live is not None:
        live_path.parent.mkdir(parents=True)
        live_path.write_text(live, encoding="utf-8")
    return template, live_path


def external_env(**overrides):
    env = {
        "LLM_BACKEND": "external",
        "ODS_MODEL_SWITCHBOARD": "observe",
        "EXTERNAL_LLM_MODEL": "provider/model-a",
        "HERMES_LLM_BASE_URL": "http://litellm:4000/v1",
        "HERMES_LLM_API_KEY": 'private-"key\\value',
        "MAX_CONTEXT": "65536",
    }
    env.update(overrides)
    return env


def test_external_addback_patches_template_before_first_start(tmp_path, monkeypatch, caplog):
    template, live_path = write_install(tmp_path, external_env())
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)

    assert agent._prepare_hermes_route_for_start() == (True, "")
    model = yaml.safe_load(template.read_text(encoding="utf-8"))["model"]
    assert model["default"] == "provider/model-a"
    assert model["base_url"] == "http://litellm:4000/v1"
    assert "api_key" not in model
    assert model["context_length"] == 65536
    assert live_path.is_file()
    private_model = yaml.safe_load(live_path.read_text(encoding="utf-8"))["model"]
    assert private_model["api_key"] == 'private-"key\\value'
    if os.name != "nt":
        assert stat.S_IMODE(live_path.stat().st_mode) == 0o600
    assert 'private-"key\\value' not in caplog.text
    first = template.read_bytes(), live_path.read_bytes()
    assert agent._prepare_hermes_route_for_start() == (True, "")
    assert (template.read_bytes(), live_path.read_bytes()) == first


def test_external_addback_updates_route_but_retains_owner_state(tmp_path, monkeypatch):
    template, live_path = write_install(
        tmp_path, external_env(),
        live='model:\n  default: "old-model"\n  provider: "custom"\n'
             '  base_url: "http://llama-server:8080/v1"\n'
             '  context_length: 131072\nother:\n  owner_value: retained\n',
    )
    sentinel = live_path.parent / "sessions" / "owner.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)

    assert agent._prepare_hermes_route_for_start() == (True, "")
    config = yaml.safe_load(live_path.read_text(encoding="utf-8"))
    assert config["model"]["default"] == "provider/model-a"
    assert config["model"]["base_url"] == "http://litellm:4000/v1"
    assert config["model"]["api_key"] == 'private-"key\\value'
    assert config["other"]["owner_value"] == "retained"
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert yaml.safe_load(template.read_text(encoding="utf-8"))["model"]["base_url"] == "http://litellm:4000/v1"
    assert "api_key" not in yaml.safe_load(template.read_text(encoding="utf-8"))["model"]


def test_local_addback_patches_template_and_preserves_existing_live_config(tmp_path, monkeypatch):
    template, live_path = write_install(
        tmp_path,
        {"LLM_BACKEND": "llama-server", "ODS_MODEL_SWITCHBOARD": "enabled",
         "GGUF_FILE": "local.gguf", "HERMES_LLM_BASE_URL": "http://model-router:9099/v1",
         "HERMES_LLM_API_KEY": "no-key", "MAX_CONTEXT": "131072"},
        live='model:\n  default: "owner-model"\n  base_url: "http://owner:8080/v1"\n',
    )
    before = live_path.read_bytes()
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)

    assert agent._prepare_hermes_route_for_start() == (True, "")
    assert live_path.read_bytes() == before
    model = yaml.safe_load(template.read_text(encoding="utf-8"))["model"]
    assert model["default"] == "ods/current"
    assert model["base_url"] == "http://model-router:9099/v1"
    assert "api_key" not in model


def test_external_addback_fails_before_start_without_gateway_key(tmp_path, monkeypatch):
    template, _ = write_install(tmp_path, external_env(HERMES_LLM_API_KEY=""))
    before = template.read_bytes()
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    assert agent._prepare_hermes_route_for_start() == (False, "Hermes external gateway key is missing")
    assert template.read_bytes() == before


def test_external_model_identity_wins_even_if_switchboard_setting_is_stale():
    assert agent._hermes_selected_model(external_env(ODS_MODEL_SWITCHBOARD="enabled")) == "provider/model-a"


def test_quoted_api_key_is_replaced_without_duplicate_and_ambiguous_owner_file_fails(tmp_path, monkeypatch):
    _, live_path = write_install(
        tmp_path, external_env(),
        live='model:\n  default: "old"\n  "api_key" : "old-secret"\n',
    )
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    assert agent._prepare_hermes_route_for_start() == (True, "")
    assert yaml.safe_load(live_path.read_text(encoding="utf-8"))["model"]["api_key"] == 'private-"key\\value'
    assert live_path.read_text(encoding="utf-8").count("api_key:") == 1

    live_path.write_text('model:\n  api_key: "first"\n  "api_key": "second"\n', encoding="utf-8")
    before = live_path.read_bytes()
    assert agent._prepare_hermes_route_for_start()[0] is False
    assert live_path.read_bytes() == before


def test_external_compose_plan_refuses_stale_local_overlay(tmp_path, monkeypatch):
    write_install(tmp_path, external_env())
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    assert agent._hermes_compose_plan_error(["-f", "extensions/services/hermes/compose.local.yaml"])
    assert agent._hermes_compose_plan_error(["-f", "extensions/services/hermes/compose.yaml"]) == ""


def test_hermes_start_prepares_route_before_compose_up(monkeypatch):
    order = []
    monkeypatch.setattr(agent, "resolve_compose_flags", lambda: [])
    monkeypatch.setattr(agent, "_hermes_compose_plan_error", lambda flags: "")
    monkeypatch.setattr(agent, "_prepare_hermes_route_for_start", lambda: (order.append("route") or (True, "")))
    monkeypatch.setattr(agent, "_precreate_data_dirs", lambda service: None)
    monkeypatch.setattr(agent, "_repair_rootless_data_ownership", lambda service: None)
    monkeypatch.setattr(agent, "_find_ext_dir", lambda service: None)
    monkeypatch.setattr(agent.subprocess, "run", lambda command, **kwargs: (
        order.append("compose") or subprocess.CompletedProcess(command, 0, "", "")))

    assert agent.docker_compose_action("hermes", "start") == (True, "")
    assert order == ["route", "compose"]


def test_hermes_external_plan_keeps_search_without_managed_llama():
    service_dir = ODS_ROOT / "extensions/services/hermes"
    manifest = yaml.safe_load((service_dir / "manifest.yaml").read_text(encoding="utf-8"))
    catalog = json.loads((ODS_ROOT / "config/extensions-catalog.json").read_text(encoding="utf-8"))
    entry = next(item for item in catalog["extensions"] if item["id"] == "hermes")
    assert manifest["service"]["depends_on"] == entry["depends_on"] == ["searxng"]
    overlay = yaml.safe_load((service_dir / "compose.local.yaml").read_text(encoding="utf-8"))
    assert overlay["services"]["hermes"]["depends_on"]["llama-server"] == {
        "condition": "service_healthy",
    }
