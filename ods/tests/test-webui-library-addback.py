"""WebUI add-back preserves the installed choice and retained user data."""

import importlib.util
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


def compose_responses(agent, monkeypatch, *, fail_at=None, stop_succeeds=True):
    module, _ = agent
    calls = []
    state = {"running": False}

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[-2:] == ["config", "--services"]:
            return subprocess.CompletedProcess(command, 1 if fail_at == "config" else 0, stdout="dashboard\nopen-webui\n")
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
    assert module._webui_selection_state() == {"enabled": False, "supported": True}
    original = (root / ".env").read_bytes()
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    assert module._webui_selection_state() == {"enabled": False, "supported": True}
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    assert module._webui_selection_state() == {"enabled": False, "supported": False}
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
