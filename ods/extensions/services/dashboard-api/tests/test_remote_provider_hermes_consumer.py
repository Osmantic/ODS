"""Remote activation must move the persisted and running Hermes consumer."""

import json
import stat

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
    monkeypatch.setattr(agent, "_read_hermes_container_config", lambda: live.read_text())
    monkeypatch.setattr(agent, "_verify_litellm_route", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "_litellm_route_failure", lambda _: None)
    monkeypatch.setattr(agent, "_render_remote_provider_cloud_config", lambda *a: None)
    monkeypatch.setattr(agent, "_managed_pixel_runtime_contract", lambda: pixel["value"])
    monkeypatch.setattr(agent, "_managed_pixel_thinking_control", lambda: None)
    monkeypatch.setattr(agent, "_reconcile_managed_pixel_contract", reconcile)
    payload = {
        "action": "configure", "provider": {"transport": "direct",
        "baseUrl": "https://provider.example.test", "model": "remote-model"},
        "secrets": {"apiKey": "provider-test-key"},
    }
    route = agent._plan_remote_provider_lifecycle_operation(payload)["route"]
    return {"live": live, "template": template, "route": route, "restarted": restarted,
            "original": original, "env": env, "data": data, "pixel": pixel, "payload": payload}


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


def test_disable_preserves_owner_edits_and_keeps_credentials_private(hermes_route):
    f = hermes_route
    template_inode = f["template"].stat().st_ino
    owner = (f["live"].stat().st_uid, f["live"].stat().st_gid)
    result = agent._activate_remote_provider_route(f["route"])
    assert f["template"].stat().st_ino == template_inode
    assert "gateway-test-key" not in f["template"].read_text()
    assert "gateway-test-key" not in json.dumps(result)
    assert "gateway-test-key" not in agent._remote_provider_activation_public_path().read_text()
    assert stat.S_IMODE(agent._remote_provider_activation_state_path().stat().st_mode) == 0o600
    current = yaml.safe_load(f["live"].read_text())
    current["agent"]["max_turns"] = 31
    f["live"].write_text(yaml.safe_dump(current))
    agent._deactivate_remote_provider_route()
    assert yaml.safe_load(f["live"].read_text())["agent"]["max_turns"] == 31
    assert f["template"].stat().st_ino == template_inode
    assert (f["live"].stat().st_uid, f["live"].stat().st_gid) == owner
    assert stat.S_IMODE(f["live"].stat().st_mode) == 0o600


@pytest.mark.parametrize("old_key", ["", "a'b # literal", 'double"quote'])
def test_restore_private_key_including_empty(hermes_route, old_key):
    f = hermes_route
    config = yaml.safe_load(f["live"].read_text())
    config["model"]["api_key"] = old_key
    f["live"].write_text(yaml.safe_dump(config))
    agent._activate_remote_provider_route(f["route"])
    agent._deactivate_remote_provider_route()
    assert yaml.safe_load(f["live"].read_text())["model"]["api_key"] == old_key


@pytest.mark.parametrize("null", ["null", "~", "", "# use environment", "null # use environment"])
def test_null_private_key_restores_environment_fallback(hermes_route, null):
    f = hermes_route
    f["live"].write_text(f["live"].read_text().replace("api_key: local-test-key", "api_key: " + null))
    agent._activate_remote_provider_route(f["route"])
    agent._deactivate_remote_provider_route()
    assert yaml.safe_load(f["live"].read_text())["model"]["api_key"] == "local-test-key"


@pytest.mark.parametrize("bad_key", ["bad\nkey", "x" * 16385], ids=["newline", "oversized"])
def test_invalid_previous_credential_refuses_before_transaction(hermes_route, monkeypatch, bad_key):
    f = hermes_route
    # Double-quoted YAML is also JSON, including the escaped newline.
    text = f["live"].read_text().replace("api_key: local-test-key", "api_key: " + json.dumps(bad_key))
    f["live"].write_text(text)
    assert yaml.safe_load(text)["model"]["api_key"] == bad_key
    before = {name: f[name].read_bytes() for name in ("env", "live", "template")}
    begun = []
    def begin(_env):
        begun.append(True)
    monkeypatch.setattr(agent, "_begin_pixel_model_transaction", begin)
    with pytest.raises(RuntimeError, match="Previous Hermes model route is invalid"):
        agent._activate_remote_provider_route(f["route"])
    assert not begun
    assert before == {name: f[name].read_bytes() for name in before}
    assert not f["restarted"]
    assert not agent._remote_provider_activation_state_path().exists()


@pytest.mark.parametrize("running,exists,live_exists", [(False, True, True), (False, False, True), (False, False, False)])
def test_optional_service_state_is_preserved(hermes_route, monkeypatch, running, exists, live_exists):
    f = hermes_route
    if not live_exists:
        f["live"].unlink()
    monkeypatch.setattr(agent, "_capture_container_state", lambda name:
                        {"exists": exists, "running": running} if name == "ods-hermes"
                        else {"exists": True, "running": True})
    agent._activate_remote_provider_route(f["route"])
    assert agent._remote_provider_hermes_matches(agent.load_env(f["env"]), agent._remote_provider_runtime_contract(f["route"]))
    agent._deactivate_remote_provider_route()
    assert not any(name == "ods-hermes" for name, _ in f["restarted"])
    assert f["live"].exists() is live_exists


def test_missing_gateway_key_refuses_before_mutation(hermes_route):
    f = hermes_route
    f["env"].write_text(f["env"].read_text().replace("LITELLM_KEY=gateway-test-key\n", ""))
    before = {name: f[name].read_bytes() for name in ("env", "live", "template")}
    with pytest.raises(RuntimeError, match="LiteLLM gateway key"):
        agent._activate_remote_provider_route(f["route"])
    assert before == {name: f[name].read_bytes() for name in before}
    assert not f["restarted"]


@pytest.mark.parametrize("direction", ["activate", "disable"])
@pytest.mark.parametrize("failure", ["restart", "readback", "pixel"])
def test_consumer_failure_restores_exact_files(hermes_route, monkeypatch, direction, failure):
    f = hermes_route
    if direction == "disable":
        agent._activate_remote_provider_route(f["route"])
    paths = [f["env"], f["live"], f["template"], agent._remote_provider_activation_state_path(),
             agent._remote_provider_activation_public_path()]
    before = {p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) if p.exists() else None for p in paths}
    if failure == "restart":
        restart = agent._restart_existing_container
        def fail_restart(name, state, **kw):
            if name == "ods-hermes":
                raise RuntimeError("injected restart failure")
            return restart(name, state, **kw)
        monkeypatch.setattr(agent, "_restart_existing_container", fail_restart)
    elif failure == "readback":
        reads = [0]
        def read():
            reads[0] += 1
            return "model:\n  default: stale\n" if reads[0] == 1 else f["live"].read_text()
        monkeypatch.setattr(agent, "_read_hermes_container_config", read)
    else:
        reconcile = agent._reconcile_managed_pixel_contract
        calls = [0]
        def fail_pixel(contract, **kw):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("injected Pixel failure")
            return reconcile(contract, **kw)
        monkeypatch.setattr(agent, "_reconcile_managed_pixel_contract", fail_pixel)
    with pytest.raises(RuntimeError, match="failed"):
        if direction == "activate":
            agent._activate_remote_provider_route(f["route"])
        else:
            agent._deactivate_remote_provider_route()
    assert before == {p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) if p.exists() else None for p in paths}


@pytest.mark.parametrize("drift", ["template", "private_key", "container", "missing_live"])
def test_unchanged_proof_checks_each_consumer_boundary(hermes_route, monkeypatch, drift):
    f = hermes_route
    agent._activate_remote_provider_route(f["route"])
    if drift == "template":
        f["template"].write_text(yaml.safe_dump(f["original"]))
    elif drift == "private_key":
        f["live"].write_text(f["live"].read_text().replace("gateway-test-key", "stale-key"))
    elif drift == "container":
        monkeypatch.setattr(agent, "_read_hermes_container_config", lambda: yaml.safe_dump(f["original"]))
    else:
        f["live"].unlink()
    assert agent._verify_current_remote_provider_consumers(f["route"], agent._remote_provider_runtime_contract(f["route"])) is None


def test_legacy_activation_preserves_retained_local_hermes_route(hermes_route):
    f = hermes_route
    agent._activate_remote_provider_route(f["route"])
    state = agent._read_remote_provider_activation_state()
    for key in ("hermesRoute", "hermesBaseUrl", "hermesApiKey"):
        state["previous"].pop(key)
    agent._write_remote_provider_activation_state(state)
    f["live"].write_text(yaml.safe_dump(f["original"]))
    agent._deactivate_remote_provider_route()
    assert yaml.safe_load(f["live"].read_text())["model"]["base_url"] == "http://model-router:9099/v1"
    assert "HERMES_LLM_API_KEY" not in agent.load_env(f["env"])


def test_hermes_added_while_remote_is_active_uses_remote_context_and_key(hermes_route, monkeypatch):
    f = hermes_route
    f["live"].unlink()
    monkeypatch.setattr(agent, "_capture_container_state", lambda name:
                        {"exists": False, "running": False} if name == "ods-hermes"
                        else {"exists": True, "running": True})
    agent._activate_remote_provider_route(f["route"])
    runtime = agent._remote_provider_runtime_contract(f["route"])
    monkeypatch.setattr(agent, "_active_remote_provider_pixel_runtime", lambda **_: runtime)
    # A retained file is copied back by an owner before Library add-back.
    f["live"].write_text(yaml.safe_dump(f["original"]))
    assert agent._prepare_hermes_route_for_start() == (True, "")
    model = yaml.safe_load(f["live"].read_text())["model"]
    assert model["base_url"] == "http://litellm:4000/v1"
    assert model["api_key"] == "gateway-test-key"
    assert model["context_length"] == runtime["contextLength"]
    agent._deactivate_remote_provider_route()
    assert yaml.safe_load(f["live"].read_text())["model"]["base_url"] == "http://model-router:9099/v1"


@pytest.mark.parametrize("action", ["configure", "disable", "remove"])
def test_outer_transaction_failure_restores_consumers_and_env(hermes_route, monkeypatch, action):
    f = hermes_route
    if action != "configure":
        agent._activate_remote_provider_route(f["route"])
    paths = [f["env"], f["live"], f["template"], agent._remote_provider_activation_state_path(),
             agent._remote_provider_activation_public_path()]
    before = {p: p.read_bytes() if p.exists() else None for p in paths}
    events = []
    class Transaction:
        previous = f["pixel"]["value"]
        completed = False
        target = None
        def apply(self, target):
            self.target = target
            return "reconciled"
        def verify_held(self):
            events.append("held")
        def finish(self, outcome):
            if outcome == "commit":
                raise RuntimeError("injected outer commit failure")
            events.append(outcome)
    monkeypatch.setattr(agent, "_begin_pixel_model_transaction", lambda _: Transaction())
    monkeypatch.setattr(agent, "_prove_pixel_model_contract", lambda *a: True)
    monkeypatch.setattr(agent, "_remote_provider_probe_lifecycle_test", lambda *a: None)
    payload = f["payload"] if action == "configure" else {"action": action}
    plan = agent._plan_remote_provider_lifecycle_operation(payload)
    with pytest.raises(agent._RemoteProviderApplyError, match="outer commit failure"):
        agent._apply_remote_provider_lifecycle_operation(payload, plan)
    assert before == {p: p.read_bytes() if p.exists() else None for p in paths}
    assert "rollback" in events


@pytest.mark.parametrize("stale", [False, True])
def test_enable_fast_path_requires_hermes_proof(hermes_route, monkeypatch, stale):
    f = hermes_route
    agent._activate_remote_provider_route(f["route"])
    if stale:
        f["live"].write_text(yaml.safe_dump(f["original"]))
    runtime = agent._remote_provider_runtime_contract(f["route"])
    monkeypatch.setattr(agent, "_read_remote_provider_route_state_document",
                        lambda: {"enabled": True, "status": {"proven": True}})
    monkeypatch.setattr(agent, "_remote_provider_profile_route", lambda _: f["route"])
    monkeypatch.setattr(agent, "_active_remote_provider_pixel_runtime", lambda **_: runtime)
    def probe(_):
        raise RuntimeError("entered repair instead of unchanged fast path")
    monkeypatch.setattr(agent, "_probe_saved_remote_provider_route", probe)
    plan = agent._plan_remote_provider_lifecycle_operation({"action": "enable"})
    if stale:
        with pytest.raises(RuntimeError, match="entered repair"):
            agent._apply_remote_provider_lifecycle_operation({"action": "enable"}, plan)
    else:
        assert agent._apply_remote_provider_lifecycle_operation({"action": "enable"}, plan)["unchanged"] is True


def test_container_owned_config_uses_container_writer_and_can_roll_back(hermes_route, monkeypatch):
    f = hermes_route
    original = f["live"].read_text()
    state = {"text": original}
    writes = []
    monkeypatch.setattr(agent, "_capture_hermes_live_config", lambda _: {
        "exists": True, "text": state["text"], "source": "container",
        "mode": None, "uid": None, "gid": None,
    })
    def write(text):
        writes.append(text)
        state["text"] = text
    monkeypatch.setattr(agent, "_write_hermes_container_config", write)
    monkeypatch.setattr(agent, "_read_hermes_container_config", lambda: state["text"])
    snapshot = agent._capture_remote_provider_hermes()
    agent._activate_remote_provider_route(f["route"])
    assert yaml.safe_load(state["text"])["model"]["api_key"] == "gateway-test-key"
    agent._restore_remote_provider_hermes(snapshot)
    assert state["text"] == original
    assert f["live"].read_text() == original, "A host-inaccessible config must not be overwritten by the host writer"
    assert len(writes) == 2


def test_inaccessible_absent_config_is_preserved_until_service_add_back(hermes_route, monkeypatch):
    f = hermes_route
    before = f["live"].read_bytes()
    monkeypatch.setattr(agent, "_capture_hermes_live_config", lambda _: {
        "exists": True, "text": None, "source": "deferred_absent",
    })
    monkeypatch.setattr(agent, "_capture_container_state", lambda name:
                        {"exists": name != "ods-hermes", "running": name != "ods-hermes"})
    agent._activate_remote_provider_route(f["route"])
    assert agent._remote_provider_hermes_matches(agent.load_env(f["env"]), agent._remote_provider_runtime_contract(f["route"]))
    agent._deactivate_remote_provider_route()
    assert f["live"].read_bytes() == before
    assert not any(name == "ods-hermes" for name, _ in f["restarted"])


@pytest.mark.parametrize("bad", ["model: {default: old}\n", "model:\n  default: a\nmodel:\n  default: b\n",
                                 "model:\n  api_key: a\n  api_key: b\n"])
def test_ambiguous_owner_yaml_is_rejected_without_mutation(hermes_route, bad):
    f = hermes_route
    f["live"].write_text(bad)
    before = f["env"].read_bytes()
    with pytest.raises(ValueError, match="Hermes"):
        agent._activate_remote_provider_route(f["route"])
    assert f["live"].read_text() == bad
    assert f["env"].read_bytes() == before
    assert not f["restarted"]


def test_failed_hermes_rollback_never_releases_transaction(hermes_route, monkeypatch):
    f = hermes_route
    events = []
    class Transaction:
        previous = f["pixel"]["value"]
        completed = False
        def apply(self, target):
            raise RuntimeError("injected consumer failure")
        def verify_held(self):
            events.append("held")
        def finish(self, outcome):
            events.append(outcome)
    monkeypatch.setattr(agent, "_begin_pixel_model_transaction", lambda _: Transaction())
    restore = agent._restore_container_state
    monkeypatch.setattr(agent, "_restore_container_state",
                        lambda name, state, **kw: False if name == "ods-hermes" else restore(name, state, **kw))
    with pytest.raises(agent._PixelModelTransactionUncertain, match="rollback is incomplete"):
        agent._activate_remote_provider_route(f["route"])
    assert events == ["held"]
