"""WebUI add-back preserves the installed choice and retained user data."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def agent(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "bin/ods-host-agent.py"
    spec = importlib.util.spec_from_file_location("webui_addback_agent", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module._test_real_resolve_compose_flags = module.resolve_compose_flags
    monkeypatch.setattr(module, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(module, "EXTENSIONS_DIR", tmp_path / "extensions/services")
    monkeypatch.setattr(module, "USER_EXTENSIONS_DIR", tmp_path / "data/user-extensions")
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module, "resolve_compose_flags", lambda: ["-f", "base.yml"])
    monkeypatch.setattr(module, "_wait_for_container_health", lambda *_args, **_kwargs: None)
    monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
    (tmp_path / ".env").write_text(
        "ODS_MODE=local\nENABLE_OPEN_WEBUI=false\n"
        "EXTERNAL_LLM_URL=https://model.example.test/v1\n"
        "OPEN_WEBUI_LLM_BASE_URL=http://litellm:4000/v1\n",
        encoding="utf-8",
    )
    data = tmp_path / "data/open-webui"
    data.mkdir(parents=True)
    (data / "retained-chat.db").write_bytes(b"private retained chat")
    yield module, tmp_path
    sys.modules.pop(spec.name, None)


def compose_responses(agent, monkeypatch, *, fail_at=None, stop_succeeds=True,
                      initial_running=False, dependent_services=None, malformed_recipe=False):
    module, _ = agent
    calls = []
    state = {"running": initial_running}

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[-2:] == ["config", "--services"]:
            return subprocess.CompletedProcess(command, 1 if fail_at == "config" else 0, stdout="dashboard\nopen-webui\n")
        if command[-3:] == ["config", "--format", "json"]:
            services = {"open-webui": {}, "dashboard": {}}
            services.update(dependent_services or {})
            return subprocess.CompletedProcess(command, 0, stdout="[]" if malformed_recipe else json.dumps({"services": services}))
        action = "up" if "up" in command else "stop"
        if action == "up":
            state["running"] = True  # Compose can fail after creating a container.
        elif stop_succeeds:
            state["running"] = False
        return subprocess.CompletedProcess(command, 1 if action == fail_at or (action == "stop" and not stop_succeeds) else 0)

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module, "_capture_container_state", lambda _name: state.copy())
    return calls


def test_add_back_starts_only_webui_and_keeps_retained_data(agent, monkeypatch):
    module, root = agent
    calls = compose_responses(agent, monkeypatch)
    sentinel = root / "data/open-webui/retained-chat.db"
    original_inode = sentinel.stat().st_ino

    status, result = module._enable_webui_selection()

    assert (status, result) == (200, {"enabled": True, "action": "enabled"})
    assert module.load_env(root / ".env")["ENABLE_OPEN_WEBUI"] == "true"
    assert sentinel.read_bytes() == b"private retained chat"
    assert sentinel.stat().st_ino == original_inode
    assert any(command[-4:] == ["up", "-d", "--no-deps", "open-webui"] for command, _ in calls)
    assert all("llama-server" not in command for command, _ in calls)
    assert all("COMPOSE_PROFILES" not in kwargs["env"] for _, kwargs in calls)


def test_mac_local_add_back_uses_saved_auth_and_bind(agent, monkeypatch):
    module, root = agent
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    with (root / ".env").open("a", encoding="utf-8") as env_file:
        env_file.write("BIND_ADDRESS=127.0.0.1\nWEBUI_AUTH=false\n")
    monkeypatch.setenv("BIND_ADDRESS", "0.0.0.0")
    monkeypatch.setenv("WEBUI_AUTH", "true")
    calls = compose_responses(agent, monkeypatch)

    status, result = module._enable_webui_selection()

    assert (status, result) == (200, {"enabled": True, "action": "enabled"})
    up_env = next(kwargs["env"] for command, kwargs in calls if "up" in command)
    assert up_env["BIND_ADDRESS"] == "127.0.0.1"
    assert up_env["WEBUI_AUTH"] == "false"
    assert module.load_env(root / ".env")["WEBUI_AUTH"] == "false"


@pytest.mark.parametrize("bind,proxy", [("0.0.0.0", "false"), ("127.0.0.1", "true")])
def test_mac_network_add_back_enforces_auth_before_start(agent, monkeypatch, bind, proxy):
    module, root = agent
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    with (root / ".env").open("a", encoding="utf-8") as env_file:
        env_file.write(f"BIND_ADDRESS={bind}\nENABLE_ODS_PROXY={proxy}\nWEBUI_AUTH=false\n")
    original_inode = (root / ".env").stat().st_ino
    monkeypatch.setenv("WEBUI_AUTH", "false")
    calls = compose_responses(agent, monkeypatch)

    status, result = module._enable_webui_selection()

    assert (status, result) == (200, {"enabled": True, "action": "enabled"})
    assert (root / ".env").stat().st_ino == original_inode
    assert module.load_env(root / ".env")["WEBUI_AUTH"] == "true"
    assert module.load_env(root / ".env")["ENABLE_OPEN_WEBUI"] == "true"
    assert all(kwargs["env"]["WEBUI_AUTH"] == "true" for _, kwargs in calls)
    assert (root / "data/open-webui/retained-chat.db").read_bytes() == b"private retained chat"


@pytest.mark.parametrize("fail_at", ["config", "up"])
def test_mac_network_add_back_failure_restores_auth_and_selection(agent, monkeypatch, fail_at):
    module, root = agent
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    with (root / ".env").open("a", encoding="utf-8") as env_file:
        env_file.write("BIND_ADDRESS=0.0.0.0\nWEBUI_AUTH=false\n")
    original = (root / ".env").read_bytes()
    calls = compose_responses(agent, monkeypatch, fail_at=fail_at)

    status, result = module._enable_webui_selection()

    assert status == 502 and result["code"] == "enable_failed"
    assert (root / ".env").read_bytes() == original
    assert (root / "data/open-webui/retained-chat.db").read_bytes() == b"private retained chat"
    assert all(kwargs["env"]["WEBUI_AUTH"] == "true" for _, kwargs in calls)


@pytest.mark.parametrize("fail_at", ["config", "up"])
def test_failed_add_back_restores_exact_selection_without_touching_data(agent, monkeypatch, fail_at):
    module, root = agent
    calls = compose_responses(agent, monkeypatch, fail_at=fail_at)
    original = (root / ".env").read_bytes()
    sentinel = root / "data/open-webui/retained-chat.db"

    status, result = module._enable_webui_selection()

    assert status == 502 and result["code"] == "enable_failed"
    assert (root / ".env").read_bytes() == original
    assert sentinel.read_bytes() == b"private retained chat"
    assert ("stop" in [item for command, _ in calls for item in command]) is (fail_at == "up")


def test_unconfirmed_stop_keeps_enabled_selection_for_reconciliation(agent, monkeypatch):
    module, root = agent
    compose_responses(agent, monkeypatch, fail_at="up", stop_succeeds=False)

    status, result = module._enable_webui_selection()

    assert status == 503 and result["code"] == "reconciliation_required"
    assert module.load_env(root / ".env")["ENABLE_OPEN_WEBUI"] == "true"


def test_failed_start_uses_same_installed_selectors_for_rollback(agent, monkeypatch):
    module, root = agent
    monkeypatch.setenv("ENABLE_OPEN_WEBUI", "false")
    monkeypatch.setenv("EXTERNAL_LLM_URL", "https://stale.example.test/v1")
    calls = compose_responses(agent, monkeypatch, fail_at="up")

    status, result = module._enable_webui_selection()

    assert status == 502 and result["code"] == "enable_failed"
    assert module.load_env(root / ".env")["ENABLE_OPEN_WEBUI"] == "false"
    up_env = next(kwargs["env"] for command, kwargs in calls if "up" in command)
    stop_env = next(kwargs["env"] for command, kwargs in calls if "stop" in command)
    assert up_env == stop_env
    assert up_env["ENABLE_OPEN_WEBUI"] == "true"
    assert up_env["EXTERNAL_LLM_URL"] == "https://model.example.test/v1"


def test_selection_reports_mac_support_and_rejects_windows(agent, monkeypatch):
    module, root = agent
    assert module._webui_selection_state() == {"enabled": False, "supported": True, "disable_supported": False}
    original = (root / ".env").read_bytes()
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    assert module._webui_selection_state() == {"enabled": False, "supported": True, "disable_supported": False}
    assert module._disable_webui_selection()[0] == 501
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    assert module._webui_selection_state() == {"enabled": False, "supported": False, "disable_supported": False}
    assert module._enable_webui_selection()[0] == 501
    assert (root / ".env").read_bytes() == original


def test_mac_add_back_uses_existing_transaction_and_retains_data(agent, monkeypatch):
    module, root = agent
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    calls = compose_responses(agent, monkeypatch)

    status, result = module._enable_webui_selection()

    assert (status, result) == (200, {"enabled": True, "action": "enabled"})
    assert (root / "data/open-webui/retained-chat.db").read_bytes() == b"private retained chat"
    assert any(command[-4:] == ["up", "-d", "--no-deps", "open-webui"] for command, _ in calls)


def test_mac_native_pixel_flags_are_added_only_for_an_active_selection(agent, monkeypatch):
    module, root = agent
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0,
            stdout="-f docker-compose.base.yml -f installers/macos/pixel-native.compose.yaml.disabled\n")
    monkeypatch.setattr(module.subprocess, "run", run)
    base = ["-f", "docker-compose.base.yml"]
    assert module._macos_native_pixel_compose_flags(base) == base
    assert not calls

    preparation = root / "data/pixel-native/preparation"
    preparation.mkdir(parents=True)
    (preparation / "activation.json").write_text("{}", encoding="utf-8")
    helper = root / "installers/macos/lib/pixel-native-stack.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("fixture", encoding="utf-8")
    assert module._macos_native_pixel_compose_flags(base) == [
        "-f", "docker-compose.base.yml", "-f", "installers/macos/pixel-native.compose.yaml.disabled"]
    assert calls[0][0] == [sys.executable, str(helper), "--install-dir", str(root),
                           "--flags", "-f docker-compose.base.yml"]
    assert calls[0][1]["cwd"] == str(root)


def test_mac_native_pixel_selection_failure_is_not_silently_ignored(agent, monkeypatch):
    module, root = agent
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    preparation = root / "data/pixel-native/preparation"
    preparation.mkdir(parents=True)
    (preparation / "selection-update.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="selector is unavailable"):
        module._macos_native_pixel_compose_flags(["-f", "docker-compose.base.yml"])
    helper = root / "installers/macos/lib/pixel-native-stack.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 1, "", "invalid receipt"))
    with pytest.raises(RuntimeError, match="needs recovery"):
        module._macos_native_pixel_compose_flags(["-f", "docker-compose.base.yml"])


@pytest.mark.parametrize("cached", [True, False])
def test_mac_host_agent_resolver_keeps_native_pixel_selection(agent, monkeypatch, cached):
    module, root = agent
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    preparation = root / "data/pixel-native/preparation"
    preparation.mkdir(parents=True)
    (preparation / "activation.json").write_text("{}", encoding="utf-8")
    helper = root / "installers/macos/lib/pixel-native-stack.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("fixture", encoding="utf-8")
    if cached:
        (root / ".compose-flags").write_text("-f docker-compose.base.yml\n", encoding="utf-8")
    else:
        resolver = root / "scripts/resolve-compose-stack.sh"
        resolver.parent.mkdir(parents=True)
        resolver.write_text("fixture", encoding="utf-8")
        monkeypatch.setattr(module, "_find_usable_bash", lambda: "/bin/bash")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        output = ("-f docker-compose.base.yml\n" if str(command[1]).endswith("resolve-compose-stack.sh")
                  else "-f docker-compose.base.yml -f installers/macos/pixel-native.compose.yaml.disabled\n")
        return subprocess.CompletedProcess(command, 0, stdout=output)
    monkeypatch.setattr(module.subprocess, "run", run)

    assert module._test_real_resolve_compose_flags() == [
        "-f", "docker-compose.base.yml", "-f", "installers/macos/pixel-native.compose.yaml.disabled"]
    assert calls[-1][1] == str(helper)
    assert len(calls) == (1 if cached else 2)


def test_mac_add_back_restores_selection_if_native_pixel_resolution_fails(agent, monkeypatch):
    module, root = agent
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    original = (root / ".env").read_bytes()
    monkeypatch.setattr(module, "resolve_compose_flags",
                        lambda: (_ for _ in ()).throw(RuntimeError("native Pixel needs recovery")))
    status, result = module._enable_webui_selection()
    assert status == 502 and result["code"] == "enable_failed"
    assert (root / ".env").read_bytes() == original
    assert (root / "data/open-webui/retained-chat.db").read_bytes() == b"private retained chat"


def test_stale_add_request_does_not_claim_running_webui(agent, monkeypatch):
    module, root = agent
    (root / ".env").write_text("ENABLE_OPEN_WEBUI=true\n", encoding="utf-8")
    monkeypatch.setattr(module, "_capture_container_state", lambda _name: {"running": False})

    status, result = module._enable_webui_selection()

    assert status == 503 and result["code"] == "selected_but_stopped"
    assert module.load_env(root / ".env")["ENABLE_OPEN_WEBUI"] == "true"


def _select_webui_with_portal(module, root, monkeypatch):
    path = root / ".env"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "ENABLE_OPEN_WEBUI=false", "ENABLE_OPEN_WEBUI=true") + "PIXEL_AGENT_MODE=pixel\n",
        encoding="utf-8")
    monkeypatch.setattr(module, "_webui_portal_ready", lambda: True)
    return path


def test_portal_readiness_checks_runtime_units_edge_and_private_ingress(agent, monkeypatch):
    module, _root = agent
    calls = []
    monkeypatch.setattr(module, "_capture_container_state", lambda _container: {"running": True})

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module._webui_portal_ready() is True
    assert any(command[:3] == ["systemctl", "is-active", "--quiet"] for command in calls)
    assert any("--unix-socket" in command for command in calls)


def test_disable_stops_webui_and_keeps_chat_data_and_bound_env(agent, monkeypatch):
    module, root = agent
    env_path = _select_webui_with_portal(module, root, monkeypatch)
    original_inode = env_path.stat().st_ino
    calls = compose_responses(agent, monkeypatch, initial_running=True)

    assert module._webui_selection_state()["disable_supported"] is True
    status, result = module._disable_webui_selection()

    assert (status, result) == (200, {"enabled": False, "action": "disabled"})
    assert env_path.stat().st_ino == original_inode
    assert module.load_env(env_path)["ENABLE_OPEN_WEBUI"] == "false"
    assert (root / "data/open-webui/retained-chat.db").read_bytes() == b"private retained chat"
    assert any(command[-2:] == ["stop", "open-webui"] for command, _ in calls)
    assert not any("up" in command for command, _ in calls)


@pytest.mark.parametrize("dependency", ["ENABLE_RAG=true", "ENABLE_ODS_PROXY=true", "PIXEL_AGENT_MODE=disabled"])
def test_disable_refuses_when_webui_is_still_required(agent, monkeypatch, dependency):
    module, root = agent
    env_path = _select_webui_with_portal(module, root, monkeypatch)
    if dependency.startswith("PIXEL_AGENT_MODE"):
        env_path.write_text(env_path.read_text(encoding="utf-8").replace("PIXEL_AGENT_MODE=pixel", dependency), encoding="utf-8")
    else:
        with env_path.open("a", encoding="utf-8") as handle:
            handle.write(dependency + "\n")
    original = env_path.read_bytes()
    calls = compose_responses(agent, monkeypatch, initial_running=True)

    status, result = module._disable_webui_selection()

    assert status == 409 and result["code"] == "webui_required"
    assert env_path.read_bytes() == original
    assert calls == []


def test_disable_refuses_when_portal_is_not_ready(agent, monkeypatch):
    module, root = agent
    env_path = _select_webui_with_portal(module, root, monkeypatch)
    monkeypatch.setattr(module, "_webui_portal_ready", lambda: False)
    original = env_path.read_bytes()
    calls = compose_responses(agent, monkeypatch, initial_running=True)

    status, result = module._disable_webui_selection()

    assert status == 409 and result["code"] == "portal_unavailable"
    assert env_path.read_bytes() == original
    assert calls == []


@pytest.mark.parametrize("service", ["qdrant", "embeddings", "ods-proxy"])
def test_disable_refuses_active_webui_dependent_extensions(agent, monkeypatch, service):
    module, root = agent
    env_path = _select_webui_with_portal(module, root, monkeypatch)
    extension = root / "extensions/services" / service
    extension.mkdir(parents=True)
    (extension / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    original = env_path.read_bytes()
    calls = compose_responses(agent, monkeypatch, initial_running=True)

    status, result = module._disable_webui_selection()

    assert status == 409 and result["code"] == "webui_required"
    assert env_path.read_bytes() == original
    assert calls == []


def test_disable_refuses_a_selected_custom_service_dependency(agent, monkeypatch):
    module, root = agent
    env_path = _select_webui_with_portal(module, root, monkeypatch)
    original = env_path.read_bytes()
    calls = compose_responses(agent, monkeypatch, initial_running=True,
                              dependent_services={"owner-service": {"depends_on": {"open-webui": {"condition": "service_started"}}}})

    status, result = module._disable_webui_selection()

    assert status == 409 and result["code"] == "webui_required"
    assert env_path.read_bytes() == original
    assert not any(command[-2:] == ["stop", "open-webui"] for command, _ in calls)


def test_disable_refuses_unverifiable_compose_graph_before_stop(agent, monkeypatch):
    module, root = agent
    env_path = _select_webui_with_portal(module, root, monkeypatch)
    original = env_path.read_bytes()
    calls = compose_responses(agent, monkeypatch, initial_running=True, malformed_recipe=True)

    status, result = module._disable_webui_selection()

    assert status == 502 and result["code"] == "disable_failed"
    assert env_path.read_bytes() == original
    assert not any(command[-2:] == ["stop", "open-webui"] for command, _ in calls)


def test_disable_partial_env_write_restores_exact_bytes_and_service(agent, monkeypatch):
    module, root = agent
    env_path = _select_webui_with_portal(module, root, monkeypatch)
    original = env_path.read_bytes()
    original_inode = env_path.stat().st_ino
    calls = compose_responses(agent, monkeypatch, initial_running=True)

    def fail_after_prefix(path, _text):
        with path.open("r+b") as handle:
            handle.write(b"partial")
            handle.truncate()
        raise RuntimeError("partial write")

    monkeypatch.setattr(module, "_write_bound_env_text", fail_after_prefix)
    status, result = module._disable_webui_selection()

    assert status == 502 and result["code"] == "disable_failed"
    assert env_path.read_bytes() == original
    assert env_path.stat().st_ino == original_inode
    assert [command[-4:] for command, _ in calls if "up" in command] == [["up", "-d", "--no-deps", "open-webui"]]
    assert (root / "data/open-webui/retained-chat.db").read_bytes() == b"private retained chat"


def test_disable_stop_failure_does_not_flip_selection(agent, monkeypatch):
    module, root = agent
    env_path = _select_webui_with_portal(module, root, monkeypatch)
    original = env_path.read_bytes()
    calls = compose_responses(agent, monkeypatch, fail_at="stop", stop_succeeds=False, initial_running=True)

    status, result = module._disable_webui_selection()

    assert status == 502 and result["code"] == "disable_failed"
    assert env_path.read_bytes() == original
    assert not any("up" in command for command, _ in calls)


def test_disable_recovers_when_stop_state_probe_fails_once(agent, monkeypatch):
    module, root = agent
    env_path = _select_webui_with_portal(module, root, monkeypatch)
    original = env_path.read_bytes()
    calls = compose_responses(agent, monkeypatch, initial_running=True)
    probes = 0

    def capture(_container):
        nonlocal probes
        probes += 1
        if probes == 2:
            raise RuntimeError("Docker inspect timed out after stop")
        return {"running": probes == 1}

    monkeypatch.setattr(module, "_capture_container_state", capture)
    status, result = module._disable_webui_selection()

    assert status == 502 and result["code"] == "disable_failed"
    assert env_path.read_bytes() == original
    assert any(command[-4:] == ["up", "-d", "--no-deps", "open-webui"] for command, _ in calls)


def test_disable_keeps_selection_when_stop_outcome_remains_unknown(agent, monkeypatch):
    module, root = agent
    env_path = _select_webui_with_portal(module, root, monkeypatch)
    original = env_path.read_bytes()
    compose_responses(agent, monkeypatch, initial_running=True)
    probes = 0

    def capture(_container):
        nonlocal probes
        probes += 1
        if probes > 1:
            raise RuntimeError("Docker inspect unavailable")
        return {"running": True}

    monkeypatch.setattr(module, "_capture_container_state", capture)
    status, result = module._disable_webui_selection()

    assert status == 503 and result["code"] == "reconciliation_required"
    assert env_path.read_bytes() == original


def test_disable_is_noop_when_unselected_and_stopped(agent, monkeypatch):
    module, root = agent
    original = (root / ".env").read_bytes()
    calls = compose_responses(agent, monkeypatch)

    assert module._disable_webui_selection() == (200, {"enabled": False, "action": "already_disabled"})
    assert (root / ".env").read_bytes() == original
    assert calls == []
