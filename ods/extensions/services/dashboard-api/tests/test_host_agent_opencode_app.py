"""Host-agent OpenCode application lifecycle: status, start, and Linux setup."""

import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_AGENT_PATH = Path(__file__).resolve().parents[4] / "bin" / "ods-host-agent.py"
_REPO_ODS = Path(__file__).resolve().parents[4]
_mod = sys.modules.get("ods_host_agent")
if _mod is None:
    _spec = importlib.util.spec_from_file_location("ods_host_agent", _AGENT_PATH)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["ods_host_agent"] = _mod
    _spec.loader.exec_module(_mod)


class _Handler:
    """Minimal stand-in for BaseHTTPRequestHandler."""

    def __init__(self, token="test-key"):
        self.headers = {"Content-Length": "0"}
        if token is not None:
            self.headers["Authorization"] = f"Bearer {token}"
        self.rfile = io.BytesIO(b"")
        self.wfile = io.BytesIO()
        self.code = None
        self.sent_headers = []

    def send_response(self, code):
        self.code = code

    def send_header(self, name, value):
        self.sent_headers.append((name, value))

    def end_headers(self):
        pass

    def body(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
    monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path / "ods")
    monkeypatch.setattr(_mod, "DATA_DIR", tmp_path / "ods" / "data")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    config_dir = home / ".config" / "opencode"
    monkeypatch.setattr(
        _mod, "_opencode_config_paths",
        lambda: (config_dir / "opencode.json", config_dir / "config.json"),
    )
    monkeypatch.setattr(_mod, "_opencode_setup_thread", None)
    yield home
    assert not _mod._model_lifecycle_lock.locked(), "OpenCode action leaked the lifecycle lock"


def _serve(handler_body, status=200, content_type="application/json"):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib naming
            payload = handler_body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# --- probe ------------------------------------------------------------------


def test_probe_reports_opencode_health_and_version():
    server = _serve(json.dumps({"healthy": True, "version": "1.18.32"}))
    try:
        probe = _mod._probe_opencode_web(server.server_address[1])
    finally:
        server.shutdown()
    assert probe["reachable"] is True
    assert probe["healthy"] is True
    assert probe["version"] == "1.18.32"


def test_probe_never_presents_another_program_as_opencode():
    server = _serve("<html>not opencode</html>", content_type="text/html")
    try:
        probe = _mod._probe_opencode_web(server.server_address[1])
    finally:
        server.shutdown()
    assert probe == {**probe, "reachable": True, "healthy": False, "version": None}


def test_probe_ignores_unsafe_version_text():
    server = _serve(json.dumps({"healthy": True, "version": "<script>"}))
    try:
        probe = _mod._probe_opencode_web(server.server_address[1])
    finally:
        server.shutdown()
    assert probe["healthy"] is True
    assert probe["version"] is None


def test_probe_reports_closed_port_as_unreachable():
    with socket.socket() as probe_socket:
        probe_socket.bind(("127.0.0.1", 0))
        port = probe_socket.getsockname()[1]
    probe = _mod._probe_opencode_web(port, timeout=0.5)
    assert probe["reachable"] is False
    assert probe["healthy"] is False


# --- lifecycle status -----------------------------------------------------------


def _status_with(monkeypatch, *, probe, registered, active=None, setup_running=False, issue=None):
    monkeypatch.setattr(_mod, "_opencode_port", lambda: 3003)
    monkeypatch.setattr(_mod, "_probe_opencode_web", lambda port: {**probe, "response_time_ms": 1.0})
    monkeypatch.setattr(_mod, "_opencode_service_registered", lambda system=None: registered)
    calls = []

    def service_active():
        calls.append("service")
        return active

    monkeypatch.setattr(_mod, "_opencode_service_active", service_active)
    monkeypatch.setattr(_mod, "_opencode_setup_in_progress", lambda: setup_running)
    monkeypatch.setattr(_mod, "_opencode_setup_issue", lambda env, system=None: issue)
    return _mod._opencode_app_status({}), calls


UNREACHABLE = {"reachable": False, "healthy": False, "version": None}
HEALTHY = {"reachable": True, "healthy": True, "version": "1.18.32"}


def test_running_opencode_requires_its_registered_active_service(monkeypatch):
    status, calls = _status_with(monkeypatch, probe=HEALTHY, registered=True, active=True)
    assert status["state"] == "running"
    assert status["version"] == "1.18.32"
    assert status["installed"] is True
    assert calls == ["service"]


def test_healthy_unmanaged_opencode_is_not_adopted(monkeypatch):
    status, calls = _status_with(monkeypatch, probe=HEALTHY, registered=False)
    assert status["state"] == "not_installed"
    assert status["installed"] is False
    assert status["healthy"] is False
    assert status["portInUse"] is True
    assert status["setupSupported"] is False
    assert calls == []


def test_healthy_port_does_not_mask_a_stopped_managed_service(monkeypatch):
    status, calls = _status_with(monkeypatch, probe=HEALTHY, registered=True, active=False)
    assert status["state"] == "stopped"
    assert status["healthy"] is False
    assert status["portInUse"] is True
    assert calls == ["service"]


def test_never_set_up_is_not_installed_rather_than_offline(monkeypatch):
    status, calls = _status_with(monkeypatch, probe=UNREACHABLE, registered=False)
    assert status["state"] == "not_installed"
    assert status["installed"] is False
    assert status["startSupported"] is False
    assert status["setupSupported"] is True
    assert calls == []


def test_registered_but_inactive_service_is_stopped(monkeypatch):
    status, _ = _status_with(monkeypatch, probe=UNREACHABLE, registered=True, active=False)
    assert status["state"] == "stopped"
    assert status["startSupported"] is True
    assert status["portInUse"] is False


def test_active_service_without_health_is_starting(monkeypatch):
    status, _ = _status_with(monkeypatch, probe=UNREACHABLE, registered=True, active=True)
    assert status["state"] == "starting"


def test_unreadable_service_manager_is_treated_as_stopped(monkeypatch):
    status, _ = _status_with(monkeypatch, probe=UNREACHABLE, registered=True, active=None)
    assert status["state"] == "stopped"


def test_other_program_on_the_port_is_flagged(monkeypatch):
    status, _ = _status_with(
        monkeypatch, probe={"reachable": True, "healthy": False, "version": None},
        registered=True, active=False,
    )
    assert status["state"] == "stopped"
    assert status["portInUse"] is True


def test_dashboard_setup_in_progress_is_installing(monkeypatch):
    status, _ = _status_with(monkeypatch, probe=UNREACHABLE, registered=False, setup_running=True)
    assert status["state"] == "installing"


def test_setup_issue_is_reported(monkeypatch):
    status, _ = _status_with(
        monkeypatch, probe=UNREACHABLE, registered=False, issue="needs systemd",
    )
    assert status["setupSupported"] is False
    assert status["setupIssue"] == "needs systemd"


@pytest.mark.parametrize("stdout,returncode,expected", [
    ("\tstate = running\n", 0, True),
    ("\tstate = not running\n", 0, False),
    ("", 113, False),
])
def test_macos_activity_requires_a_running_process(monkeypatch, stdout, returncode, expected):
    monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(_mod.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(
        _mod.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=""),
    )
    assert _mod._opencode_service_active() is expected


def test_linux_activity_uses_the_managed_unit_state(monkeypatch):
    monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(_mod, "_capture_managed_opencode_state", lambda: {"system": "Linux", "active": True})
    assert _mod._opencode_service_active() is True

    def unreadable():
        raise RuntimeError("Failed to connect to bus")

    monkeypatch.setattr(_mod, "_capture_managed_opencode_state", unreadable)
    assert _mod._opencode_service_active() is None


def test_registration_follows_each_platform_service_manager(_isolated):
    home = _isolated
    assert _mod._opencode_service_registered("Linux") is False
    unit = home / ".config" / "systemd" / "user" / "opencode-web.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Unit]\n")
    assert _mod._opencode_service_registered("Linux") is True

    assert _mod._opencode_service_registered("Darwin") is False
    plist = home / "Library" / "LaunchAgents" / "com.ods.opencode-web.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist/>")
    assert _mod._opencode_service_registered("Darwin") is True

    assert _mod._opencode_service_registered("Windows") is False
    exe = home / ".opencode" / "bin" / "opencode.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    assert _mod._opencode_service_registered("Windows") is True


# --- setup availability ----------------------------------------------------------


def _setup_ready_install(tmp_path, monkeypatch):
    install = tmp_path / "ods"
    for relative in (
        "installers/lib/opencode-runtime.sh",
        "installers/lib/opencode-release.tsv",
    ):
        target = install / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# test\n")
    template = install / "opencode" / "opencode-web.service"
    template.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_REPO_ODS / "opencode" / "opencode-web.service", template)
    runtime = tmp_path / "run-user"
    runtime.mkdir()
    (runtime / "bus").write_text("")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    return install


def test_setup_is_offered_on_linux_with_a_user_session(tmp_path, monkeypatch):
    _setup_ready_install(tmp_path, monkeypatch)
    assert _mod._opencode_setup_issue({"ODS_MODEL_SWITCHBOARD": "enabled"}, "Linux") is None


@pytest.mark.parametrize("system", ["Darwin", "Windows"])
def test_setup_defers_to_the_installer_off_linux(system):
    assert "installer" in _mod._opencode_setup_issue({}, system)


def test_setup_requires_systemctl(tmp_path, monkeypatch):
    _setup_ready_install(tmp_path, monkeypatch)
    monkeypatch.setattr(_mod.shutil, "which", lambda name: None)
    assert "systemctl" in _mod._opencode_setup_issue({"ODS_MODEL_SWITCHBOARD": "enabled"}, "Linux")


def test_setup_requires_a_user_bus(tmp_path, monkeypatch):
    _setup_ready_install(tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "missing"))
    assert "enable-linger" in _mod._opencode_setup_issue({"ODS_MODEL_SWITCHBOARD": "enabled"}, "Linux")


def test_setup_requires_the_shipped_unit_template(tmp_path, monkeypatch):
    install = _setup_ready_install(tmp_path, monkeypatch)
    (install / "opencode" / "opencode-web.service").unlink()
    assert "opencode-web.service" in _mod._opencode_setup_issue({"ODS_MODEL_SWITCHBOARD": "enabled"}, "Linux")


def test_setup_offers_an_external_route_without_the_switchboard(tmp_path, monkeypatch):
    _setup_ready_install(tmp_path, monkeypatch)
    env = {
        "ODS_MODEL_SWITCHBOARD": "observe",
        "EXTERNAL_LLM_URL": "https://llm.example.test/v1",
        "EXTERNAL_LLM_MODEL": "remote",
        "LITELLM_PORT": "4400",
        "LITELLM_KEY": "test-key",
        "OLLAMA_PORT": "8080",
    }
    assert _mod._opencode_setup_issue(env, "Linux") is None
    assert _mod._opencode_route(env) == ("http://127.0.0.1:4400/v1", "test-key")
    assert _mod._opencode_model_route(env, "local") == ("llama-server", "remote", "remote")
    assert "gateway key" in _mod._opencode_setup_issue({**env, "LITELLM_KEY": ""}, "Linux")
    with pytest.raises(RuntimeError, match="LITELLM_KEY"):
        _mod._opencode_route({**env, "LITELLM_KEY": ""})


def test_setup_needs_a_model_without_the_switchboard(tmp_path, monkeypatch):
    _setup_ready_install(tmp_path, monkeypatch)
    assert "Activate a model" in _mod._opencode_setup_issue({"ODS_MODEL_SWITCHBOARD": "observe"}, "Linux")
    assert _mod._opencode_setup_issue({"ODS_MODEL_SWITCHBOARD": "observe", "LLM_MODEL": "qwen"}, "Linux") is None


# --- start -----------------------------------------------------------------------


def _fixed_status(monkeypatch, **overrides):
    status = {
        "state": "stopped", "registered": True, "portInUse": False, "port": 3003,
        **overrides,
    }
    monkeypatch.setattr(_mod, "_opencode_app_status", lambda env=None: dict(status))
    return status


def test_start_is_a_no_op_when_already_running(monkeypatch):
    _fixed_status(monkeypatch, state="running")
    monkeypatch.setattr(_mod, "_start_managed_opencode", lambda: pytest.fail("must not start"))
    code, body = _mod._begin_opencode_start({})
    assert code == 200
    assert body["started"] is False


def test_start_refuses_when_opencode_was_never_set_up(monkeypatch):
    _fixed_status(monkeypatch, state="not_installed", registered=False)
    code, body = _mod._begin_opencode_start({})
    assert code == 409
    assert body["code"] == "opencode_not_installed"


def test_start_refuses_when_another_program_holds_the_port(monkeypatch):
    _fixed_status(monkeypatch, portInUse=True)
    code, body = _mod._begin_opencode_start({})
    assert code == 409
    assert body["code"] == "opencode_port_in_use"
    assert "3003" in body["error"]


def test_start_runs_the_service_manager_under_the_lifecycle_lock(monkeypatch):
    _fixed_status(monkeypatch)
    seen = []
    monkeypatch.setattr(_mod, "_start_managed_opencode", lambda: seen.append(_mod._model_lifecycle_lock.locked()))
    code, body = _mod._begin_opencode_start({})
    assert code == 200
    assert body["started"] is True
    assert seen == [True]


def test_start_failure_is_reported_and_releases_the_lock(monkeypatch):
    _fixed_status(monkeypatch)

    def fail():
        raise RuntimeError("Managed OpenCode did not become healthy at http://127.0.0.1:3003/")

    monkeypatch.setattr(_mod, "_start_managed_opencode", fail)
    code, body = _mod._begin_opencode_start({})
    assert code == 502
    assert body["code"] == "opencode_start_failed"
    assert "did not become healthy" in body["error"]


def test_start_waits_for_a_model_switch(monkeypatch):
    _fixed_status(monkeypatch)
    acquired, _ = _mod._begin_model_lifecycle("model_activate", "qwen")
    assert acquired
    try:
        code, body = _mod._begin_opencode_start({})
    finally:
        _mod._end_model_lifecycle("model_activate")
    assert code == 409
    assert body["code"] == "model_lifecycle_busy"


def _record_runs(monkeypatch, returncode=0):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, returncode, stdout="", stderr="boom" if returncode else "")

    monkeypatch.setattr(_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(_mod, "_wait_for_opencode_health", lambda attempts=30: None)
    return commands


def test_linux_start_clears_a_failed_unit_then_starts_it(monkeypatch):
    monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(_mod, "_opencode_user_service_env", lambda: {"XDG_RUNTIME_DIR": "/run/user/1000"})
    commands = _record_runs(monkeypatch)
    _mod._start_managed_opencode()
    assert commands == [
        ["systemctl", "--user", "reset-failed", "opencode-web.service"],
        ["systemctl", "--user", "start", "opencode-web.service"],
    ]


def test_linux_start_failure_raises(monkeypatch):
    monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(_mod, "_opencode_user_service_env", lambda: {})
    _record_runs(monkeypatch, returncode=1)
    with pytest.raises(RuntimeError, match="Could not start OpenCode"):
        _mod._start_managed_opencode()


@pytest.mark.parametrize("loaded,expected", [
    (True, ["launchctl", "kickstart", "gui/501/com.ods.opencode-web"]),
    (False, None),
])
def test_macos_start_kicks_a_loaded_agent_or_bootstraps_it(monkeypatch, _isolated, loaded, expected):
    monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(_mod.os, "getuid", lambda: 501, raising=False)
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        code = 0 if command[1] != "print" or loaded else 113
        return subprocess.CompletedProcess(command, code, stdout="", stderr="")

    monkeypatch.setattr(_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(_mod, "_wait_for_opencode_health", lambda attempts=30: None)
    _mod._start_managed_opencode()
    plist = str(_isolated / "Library" / "LaunchAgents" / "com.ods.opencode-web.plist")
    assert commands[0] == ["launchctl", "print", "gui/501/com.ods.opencode-web"]
    assert commands[1] == (expected or ["launchctl", "bootstrap", "gui/501", plist])


def test_windows_start_uses_the_scheduled_task_control(monkeypatch):
    monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
    actions = []
    monkeypatch.setattr(_mod, "_run_windows_opencode_control", lambda action: actions.append(action) or True)
    monkeypatch.setattr(_mod, "_wait_for_opencode_health", lambda attempts=30: None)
    _mod._start_managed_opencode()
    assert actions == ["start"]


def test_windows_control_script_starts_the_installer_task(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["script"] = command[-1]
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, stdout="true\n", stderr="")

    monkeypatch.setattr(_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(_mod, "_windows_management_shell", lambda: "powershell.exe")
    monkeypatch.setattr(_mod, "_opencode_port", lambda: 3003)
    assert _mod._run_windows_opencode_control("start") is True
    assert captured["env"]["ODS_OPENCODE_ACTION"] == "start"
    assert "if ($action -eq 'start')" in captured["script"]
    assert "Start-ScheduledTask -TaskName 'ODSOpenCodeWeb'" in captured["script"]


# --- Linux setup -----------------------------------------------------------------


SETUP_ENV = {
    "ODS_MODEL_SWITCHBOARD": "enabled",
    "LITELLM_KEY": "sk-test-gateway",
    "LITELLM_PORT": "4000",
    "MAX_CONTEXT": "32768",
    "LLM_MODEL": "qwen3-8b",
}


def _prepare_setup(tmp_path, monkeypatch, home, *, installer_rc=0, fail_action=None,
                   prior_enabled=False, prior_active=False, prior_loaded=False):
    install = _setup_ready_install(tmp_path, monkeypatch)
    monkeypatch.setattr(_mod, "_opencode_user_service_env", lambda: {"XDG_RUNTIME_DIR": "/run/user/1000"})
    monkeypatch.setattr(_mod, "_wait_for_opencode_health", lambda attempts=30: None)
    # Discovery elsewhere must not matter: setup trusts the executable the
    # reviewed-release installer just returned.
    monkeypatch.setattr(_mod, "_opencode_installed", lambda: False)
    progress = []
    monkeypatch.setattr(
        _mod, "_write_progress",
        lambda service_id, status, phase_label="", error=None, **kwargs: progress.append((service_id, status, error)),
    )
    monkeypatch.setenv("USER", "ods-owner")
    commands = []
    binary = home / ".opencode" / "bin" / "opencode"
    fail_seen = False

    def fake_run(command, **kwargs):
        nonlocal fail_seen
        commands.append(command)
        if command[0] == "bash":
            assert command[4] == str(install / "installers" / "lib" / "opencode-runtime.sh")
            if installer_rc:
                return subprocess.CompletedProcess(command, installer_rc, stdout="", stderr="OpenCode archive SHA256 mismatch\n")
            binary.parent.mkdir(parents=True, exist_ok=True)
            staged = binary.with_suffix(".new")
            staged.write_text("#!/bin/sh\n")
            staged.chmod(0o755)
            os.replace(staged, binary)
            return subprocess.CompletedProcess(command, 0, stdout=f"{binary}\n", stderr="")
        if command[0] == "systemctl":
            if command[2] == "show":
                state = (
                    f"LoadState={'loaded' if prior_enabled or prior_active or prior_loaded else 'not-found'}\n"
                    f"UnitFileState={'enabled' if prior_enabled else 'disabled' if prior_active or prior_loaded else ''}\n"
                    f"ActiveState={'active' if prior_active else 'inactive'}\n"
                )
                return subprocess.CompletedProcess(command, 0, stdout=state, stderr="")
            failed = command[2] == fail_action and not fail_seen
            if failed:
                fail_seen = True
            return subprocess.CompletedProcess(command, 1 if failed else 0,
                                               stdout="", stderr="unit failed" if failed else "")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(_mod.subprocess, "run", fake_run)
    return install, commands, progress, binary


def test_default_agent_models_follow_ods_route_without_replacing_owner_models():
    config = {
        "agent": {
            "build": {"model": "custom-cloud/owner-model", "temperature": 0.2},
            "plan": {"temperature": 0.1},
            "reviewer": {"model": "custom-cloud/review-model"},
        },
    }
    _mod._opencode_set_default_agent_models(
        config, "llama-server/old-model", "llama-server/ods/current",
    )
    assert config["agent"]["build"] == {
        "model": "custom-cloud/owner-model", "temperature": 0.2,
    }
    assert config["agent"]["plan"] == {
        "model": "llama-server/ods/current", "temperature": 0.1,
    }
    assert config["agent"]["reviewer"] == {"model": "custom-cloud/review-model"}

    following = {"agent": {"build": {"model": "llama-server/old-model"}}}
    _mod._opencode_set_default_agent_models(
        following, "llama-server/old-model", "llama-server/new-model",
    )
    assert following["agent"]["build"]["model"] == "llama-server/new-model"
    assert following["agent"]["plan"]["model"] == "llama-server/new-model"


@pytest.mark.skipif(os.name == "nt", reason="systemd unit rendering requires POSIX paths")
def test_setup_installs_configures_and_starts_the_managed_service(tmp_path, monkeypatch, _isolated):
    home = _isolated
    _, commands, progress, binary = _prepare_setup(tmp_path, monkeypatch, home)

    _mod._setup_managed_opencode(dict(SETUP_ENV))

    unit = (home / ".config" / "systemd" / "user" / "opencode-web.service").read_text()
    assert f"ExecStart={binary} serve --port 3003 --hostname 127.0.0.1" in unit
    assert f"WorkingDirectory={home}" in unit
    assert "__" not in unit
    config = json.loads((home / ".config" / "opencode" / "opencode.json").read_text())
    assert config["model"] == "llama-server/ods/current"
    assert config["agent"]["build"]["model"] == "llama-server/ods/current"
    assert config["agent"]["plan"]["model"] == "llama-server/ods/current"
    provider = config["provider"]["llama-server"]
    assert provider["options"] == {"baseURL": "http://127.0.0.1:4000/v1", "apiKey": "sk-test-gateway"}
    assert provider["models"]["ods/current"]["limit"] == {"context": 32768, "output": 8192}
    assert json.loads((home / ".config" / "opencode" / "config.json").read_text()) == config
    systemctl = [command[2:] for command in commands if command[0] == "systemctl"]
    assert systemctl[0][0] == "show"
    assert systemctl[1:] == [["daemon-reload"], ["enable", "opencode-web.service"],
                            ["restart", "opencode-web.service"]]
    assert ["loginctl", "enable-linger", "ods-owner"] in commands
    assert [status for _, status, _ in progress] == ["pulling", "starting", "starting", "started"]


@pytest.mark.skipif(os.name == "nt", reason="systemd unit rendering requires POSIX paths")
def test_setup_external_model_uses_authenticated_gateway(tmp_path, monkeypatch, _isolated):
    home = _isolated
    _prepare_setup(tmp_path, monkeypatch, home)
    env = {
        **SETUP_ENV,
        "ODS_MODEL_SWITCHBOARD": "observe",
        "EXTERNAL_LLM_URL": "https://llm.example.test/v1",
        "EXTERNAL_LLM_MODEL": "remote-model",
        "LITELLM_PORT": "4400",
        "OLLAMA_PORT": "8080",
    }

    _mod._setup_managed_opencode(env)

    config = json.loads((home / ".config" / "opencode" / "opencode.json").read_text())
    assert config["model"] == "llama-server/remote-model"
    assert config["agent"]["build"]["model"] == "llama-server/remote-model"
    assert config["agent"]["plan"]["model"] == "llama-server/remote-model"
    provider = config["provider"]["llama-server"]
    assert provider["name"] == "External LLM via ODS gateway"
    assert provider["options"] == {
        "baseURL": "http://127.0.0.1:4400/v1", "apiKey": "sk-test-gateway",
    }
    assert provider["models"]["remote-model"]["limit"] == {
        "context": 32768, "output": 8192,
    }
    assert json.loads((home / ".config" / "opencode" / "config.json").read_text()) == config


@pytest.mark.skipif(os.name == "nt", reason="systemd unit rendering requires POSIX paths")
def test_setup_ignores_an_unrelated_path_binary(tmp_path, monkeypatch, _isolated):
    home = _isolated
    _, commands, _, binary = _prepare_setup(tmp_path, monkeypatch, home)

    _mod._setup_managed_opencode(dict(SETUP_ENV))

    unit = (home / ".config" / "systemd" / "user" / "opencode-web.service").read_text()
    assert f"ExecStart={binary} serve --port 3003 --hostname 127.0.0.1" in unit
    assert next(command for command in commands if command[0] == "bash")[5] == ""
    assert (home / ".config" / "opencode" / "opencode.json").is_file()


@pytest.mark.skipif(os.name == "nt", reason="systemd unit rendering requires POSIX paths")
def test_setup_download_failure_changes_nothing(tmp_path, monkeypatch, _isolated):
    home = _isolated
    _, commands, _, _ = _prepare_setup(tmp_path, monkeypatch, home, installer_rc=1)
    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        _mod._setup_managed_opencode(dict(SETUP_ENV))
    assert not (home / ".config" / "systemd" / "user" / "opencode-web.service").exists()
    assert not (home / ".config" / "opencode" / "opencode.json").exists()
    assert not (home / ".opencode" / "bin" / "opencode").exists()
    assert [command[0] for command in commands] == ["systemctl", "bash"]


@pytest.mark.skipif(os.name == "nt", reason="systemd unit rendering requires POSIX paths")
def test_setup_reports_a_failed_service_start(tmp_path, monkeypatch, _isolated):
    _prepare_setup(tmp_path, monkeypatch, _isolated, fail_action="daemon-reload")
    with pytest.raises(RuntimeError, match="daemon-reload failed: unit failed"):
        _mod._setup_managed_opencode(dict(SETUP_ENV))


@pytest.mark.skipif(os.name == "nt", reason="systemd unit rendering requires POSIX paths")
def test_setup_enable_failure_restores_previous_config_unit_and_service_state(
    tmp_path, monkeypatch, _isolated,
):
    home = _isolated
    old_unit = home / ".config" / "systemd" / "user" / "opencode-web.service"
    old_unit.parent.mkdir(parents=True)
    old_unit.write_bytes(b"[Service]\nExecStart=/old/opencode\n")
    old_unit.chmod(0o600)
    old_config = home / ".config" / "opencode" / "opencode.json"
    old_config.parent.mkdir(parents=True)
    old_config.write_bytes(b'{"model":"old/model"}\n')
    _, commands, _, _ = _prepare_setup(
        tmp_path, monkeypatch, home, fail_action="enable", prior_loaded=True,
    )
    with pytest.raises(RuntimeError, match="enable opencode-web.service failed: unit failed"):
        _mod._setup_managed_opencode(dict(SETUP_ENV))
    assert old_unit.read_bytes() == b"[Service]\nExecStart=/old/opencode\n"
    assert old_unit.stat().st_mode & 0o777 == 0o600
    assert old_config.read_bytes() == b'{"model":"old/model"}\n'
    assert not (home / ".config" / "opencode" / "config.json").exists()
    systemctl = [command[2] for command in commands if command[0] == "systemctl"]
    assert systemctl == ["show", "daemon-reload", "enable", "disable", "daemon-reload"]


@pytest.mark.skipif(os.name == "nt", reason="systemd unit rendering requires POSIX paths")
def test_setup_restart_failure_restores_new_config_and_unit(tmp_path, monkeypatch, _isolated):
    home = _isolated
    _, commands, _, binary = _prepare_setup(tmp_path, monkeypatch, home, fail_action="restart")
    with pytest.raises(RuntimeError, match="restart opencode-web.service failed: unit failed"):
        _mod._setup_managed_opencode(dict(SETUP_ENV))
    assert not (home / ".config" / "opencode" / "opencode.json").exists()
    assert not (home / ".config" / "opencode" / "config.json").exists()
    assert not (home / ".config" / "systemd" / "user" / "opencode-web.service").exists()
    assert not binary.exists()
    systemctl = [command[2] for command in commands if command[0] == "systemctl"]
    assert systemctl == ["show", "daemon-reload", "enable", "restart",
                         "stop", "disable", "daemon-reload"]


@pytest.mark.skipif(os.name == "nt", reason="systemd unit rendering requires POSIX paths")
def test_setup_failure_restores_existing_unit_without_disabling_active_service(
    tmp_path, monkeypatch, _isolated,
):
    home = _isolated
    old_unit = home / ".config" / "systemd" / "user" / "opencode-web.service"
    old_unit.parent.mkdir(parents=True)
    old_unit.write_text("[Service]\nExecStart=/old/opencode\n")
    old_unit.chmod(0o600)
    old_config = home / ".config" / "opencode" / "opencode.json"
    old_config.parent.mkdir(parents=True)
    old_config.write_text('{"model":"old/model"}\n')
    _, commands, _, _ = _prepare_setup(
        tmp_path, monkeypatch, home, fail_action="restart",
        prior_enabled=True, prior_active=True,
    )
    with pytest.raises(RuntimeError, match="restart opencode-web.service failed: unit failed"):
        _mod._setup_managed_opencode(dict(SETUP_ENV))
    assert old_unit.read_text() == "[Service]\nExecStart=/old/opencode\n"
    assert old_unit.stat().st_mode & 0o777 == 0o600
    assert old_config.read_text() == '{"model":"old/model"}\n'
    systemctl = [command[2] for command in commands if command[0] == "systemctl"]
    assert systemctl == ["show", "daemon-reload", "enable", "restart",
                         "daemon-reload", "restart"]


@pytest.mark.skipif(os.name == "nt", reason="systemd unit rendering requires POSIX paths")
def test_setup_failure_restores_previous_managed_binary(tmp_path, monkeypatch, _isolated):
    home = _isolated
    old_binary = home / ".opencode" / "bin" / "opencode"
    old_binary.parent.mkdir(parents=True)
    old_binary.write_bytes(b"old-opencode-binary")
    old_binary.chmod(0o755)
    _, commands, _, _ = _prepare_setup(tmp_path, monkeypatch, home, fail_action="restart")
    with pytest.raises(RuntimeError, match="restart opencode-web.service failed"):
        _mod._setup_managed_opencode(dict(SETUP_ENV))
    assert old_binary.read_bytes() == b"old-opencode-binary"
    assert old_binary.stat().st_mode & 0o777 == 0o755
    assert next(command for command in commands if command[0] == "bash")[5] == str(old_binary)
    assert list(old_binary.parent.glob(".ods-opencode-backup-*")) == []


def test_wait_for_health_rejects_an_unrelated_web_page(monkeypatch):
    server = _serve("<html>not OpenCode</html>", content_type="text/html")
    monkeypatch.setattr(_mod, "_opencode_port", lambda: server.server_address[1])
    try:
        with pytest.raises(RuntimeError, match="/global/health"):
            _mod._wait_for_opencode_health(attempts=1)
    finally:
        server.shutdown()


def test_setup_rejects_paths_the_unit_cannot_quote(tmp_path):
    with pytest.raises(RuntimeError, match="Unsupported path"):
        _mod._render_opencode_unit("ExecStart=__OPENCODE_BIN__", Path("/home/a b/.opencode/bin/opencode"))


def test_background_setup_records_failure_and_releases_the_lock(monkeypatch):
    monkeypatch.setattr(_mod, "_opencode_setup_issue", lambda env, system=None: None)
    _fixed_status(monkeypatch, state="not_installed", registered=False)
    progress = []
    monkeypatch.setattr(
        _mod, "_write_progress",
        lambda service_id, status, phase_label="", error=None, **kwargs: progress.append((status, error)),
    )
    held = []

    def fail(env):
        held.append(_mod._model_lifecycle_lock.locked())
        raise RuntimeError("OpenCode download or verification failed: offline")

    monkeypatch.setattr(_mod, "_setup_managed_opencode", fail)
    code, body = _mod._begin_opencode_setup({})
    assert code == 202
    assert body["status"]["state"] == "installing"
    _mod._opencode_setup_thread.join(timeout=5)
    assert held == [True]
    assert progress[-1] == ("error", "OpenCode download or verification failed: offline")


def test_setup_is_refused_when_unsupported(monkeypatch):
    monkeypatch.setattr(_mod, "_opencode_setup_issue", lambda env, system=None: "needs systemd")
    code, body = _mod._begin_opencode_setup({})
    assert code == 409
    assert body == {"error": "needs systemd", "code": "opencode_setup_unsupported"}


def test_setup_is_not_repeated_while_running(monkeypatch):
    monkeypatch.setattr(_mod, "_opencode_setup_issue", lambda env, system=None: None)
    _fixed_status(monkeypatch, state="running")
    monkeypatch.setattr(_mod, "_setup_managed_opencode", lambda env: pytest.fail("must not set up"))
    code, body = _mod._begin_opencode_setup({})
    assert code == 200
    assert body["accepted"] is False


def test_setup_refuses_a_foreign_process_on_its_port(monkeypatch):
    monkeypatch.setattr(_mod, "_opencode_setup_issue", lambda env, system=None: None)
    _fixed_status(monkeypatch, state="not_installed", registered=False, portInUse=True)
    code, body = _mod._begin_opencode_setup({})
    assert code == 409
    assert body["code"] == "opencode_port_in_use"


def test_setup_refuses_to_restart_an_already_starting_service(monkeypatch):
    monkeypatch.setattr(_mod, "_opencode_setup_issue", lambda env, system=None: None)
    _fixed_status(monkeypatch, state="starting")
    code, body = _mod._begin_opencode_setup({})
    assert code == 409
    assert body["code"] == "opencode_starting"


# --- HTTP handlers ---------------------------------------------------------------


def test_status_route_requires_the_agent_key():
    handler = _Handler(token=None)
    _mod.AgentHandler._handle_opencode_status(handler)
    assert handler.code == 401


def test_status_route_returns_the_lifecycle_uncached(monkeypatch):
    monkeypatch.setattr(_mod, "_opencode_app_status", lambda env=None: {"state": "stopped"})
    handler = _Handler()
    _mod.AgentHandler._handle_opencode_status(handler)
    assert handler.code == 200
    assert handler.body() == {"state": "stopped"}
    assert ("Cache-Control", "no-store") in handler.sent_headers


def test_action_route_maps_start_results(monkeypatch):
    monkeypatch.setattr(_mod, "load_env", lambda path: {})
    monkeypatch.setattr(_mod, "_begin_opencode_start", lambda env: (409, {"code": "opencode_not_installed"}))
    handler = _Handler()
    _mod.AgentHandler._handle_opencode_action(handler, "start")
    assert handler.code == 409
    assert handler.body() == {"code": "opencode_not_installed"}


def test_action_route_requires_the_agent_key(monkeypatch):
    monkeypatch.setattr(_mod, "_begin_opencode_setup", lambda env: pytest.fail("unauthenticated setup"))
    handler = _Handler(token="wrong")
    _mod.AgentHandler._handle_opencode_action(handler, "setup")
    assert handler.code == 403
