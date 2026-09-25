"""Install failures before start must not poison the merged Compose project.

Every enabled extension is interpolated into one Compose project. A library
extension whose ``${NAME:?}`` setting is missing, or whose image could not be
built, would otherwise stay enabled after its failed install and fail model
switches, other installs and every ``ods`` stack command. These tests drive
the host agent's real install and enable-retry workers with Docker faked.
"""

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

_agent_path = Path(__file__).resolve().parents[4] / "bin" / "ods-host-agent.py"
_spec = importlib.util.spec_from_file_location("ods_host_agent", _agent_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ods_host_agent"] = _mod
_spec.loader.exec_module(_mod)

# Verbatim `docker compose config` stderr from tower2 (2026-09-25) for the
# library gotify and bookstack recipes with no settings in .env.
GOTIFY_CONFIG_ERROR = (
    "error while interpolating services.gotify.environment.GOTIFY_DEFAULTUSER_PASS: "
    "required variable GOTIFY_ADMIN_PASSWORD is missing a value: Set the initial administrator password\n")
BOOKSTACK_CONFIG_ERROR = (
    "error while interpolating services.bookstack.environment.DB_PASSWORD: "
    "required variable BOOKSTACK_DB_PASSWORD is missing a value: Set BOOKSTACK_DB_PASSWORD\n")
GOTIFY_COMPOSE = (
    "services:\n  gotify:\n    image: ods/gotify:3.1.1-local-v1\n    environment:\n"
    "      GOTIFY_DEFAULTUSER_PASS: ${GOTIFY_ADMIN_PASSWORD:?Set the initial administrator password}\n")


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A host agent with one enabled library extension and faked Docker."""
    install_root = tmp_path / "install"
    data = tmp_path / "data"
    users = data / "user-extensions"
    builtins = tmp_path / "extensions"
    for directory in (install_root, data, users, builtins):
        directory.mkdir(parents=True)
    (install_root / ".env").write_text("SERVICE_API_KEY=persisted-credential\n", encoding="utf-8")
    (install_root / ".compose-flags").write_text("--env-file .env -f docker-compose.base.yml", encoding="utf-8")
    monkeypatch.setattr(_mod, "INSTALL_DIR", install_root)
    monkeypatch.setattr(_mod, "DATA_DIR", data)
    monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", users)
    monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtins)
    monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(_mod, "check_auth", lambda handler: True)
    monkeypatch.setattr(_mod, "validate_service_id", lambda handler, body: body["service_id"])
    responses = []
    monkeypatch.setattr(_mod, "json_response", lambda handler, status, body: responses.append((status, body)))

    class Worker:  # Run the accepted install worker synchronously.
        def __init__(self, target=None, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(_mod.threading, "Thread", Worker)
    for service_id in ("gotify", "bookstack", "hooked", "builtin-svc"):
        _mod._service_locks.pop(service_id, None)

    def extension(service_id, compose=GOTIFY_COMPOSE, *, root=users, manifest=None):
        directory = root / service_id
        directory.mkdir()
        (directory / "manifest.yaml").write_text(json.dumps(manifest or {
            "schema_version": "ods.services.v1", "service": {"id": service_id, "port": 8080}}), encoding="utf-8")
        (directory / "compose.yaml").write_text(compose, encoding="utf-8")
        return directory

    calls = []

    def docker(config_error="", build_error="", up_error=""):
        def run(command, **kwargs):
            calls.append(list(command))
            if command[:2] == ["docker", "compose"] and command[-3:] == ["config", "--format", "json"]:
                if config_error:
                    # Compose prints nothing on stdout when interpolation fails;
                    # a resolved configuration would contain credential values.
                    return types.SimpleNamespace(returncode=1, stdout="", stderr=config_error)
                image = {"build": {"context": "/ext"}} if build_error else {"image": "example/app:1"}
                return types.SimpleNamespace(returncode=0, stdout=json.dumps({"services": {
                    name: dict(image) for name in ("gotify", "bookstack", "hooked", "builtin-svc")}}), stderr="")
            if command[:2] == ["docker", "compose"] and "build" in command:
                return types.SimpleNamespace(returncode=1, stdout="", stderr=build_error)
            if command[:2] == ["docker", "compose"] and "up" in command:
                return types.SimpleNamespace(returncode=1 if up_error else 0, stdout="", stderr=up_error)
            if command[:2] == ["docker", "compose"]:
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            if command[:2] == ["docker", "inspect"]:
                return types.SimpleNamespace(returncode=0, stdout="running|", stderr="")
            raise AssertionError(f"unexpected command: {command}")
        monkeypatch.setattr(_mod.subprocess, "run", run)
        return calls

    def install(service_id, run_setup_hook=False):
        monkeypatch.setattr(_mod, "read_json_body", lambda handler: {
            "service_id": service_id, "run_setup_hook": run_setup_hook})
        _mod.AgentHandler._handle_install(object())

    def progress(service_id):
        return json.loads((data / "extension-progress" / f"{service_id}.json").read_text(encoding="utf-8"))

    return types.SimpleNamespace(install_dir=install_root, users=users, builtins=builtins, extension=extension,
                                 docker=docker, install=install, progress=progress, responses=responses)


def _started(calls):
    return [call for call in calls if call[:2] == ["docker", "compose"] and "up" in call]


def test_unresolvable_install_is_disabled_and_reports_the_missing_setting(host):
    extension = host.extension("gotify")
    calls = host.docker(config_error=GOTIFY_CONFIG_ERROR)

    host.install("gotify")

    assert host.responses[-1][0] == 202
    record = host.progress("gotify")
    assert record["status"] == "error"
    first_line = record["error"].splitlines()[0]
    assert first_line.startswith("Could not resolve installation Compose configuration; containers were not started. "
                                 "Missing required setting: GOTIFY_ADMIN_PASSWORD. ")
    assert "required variable GOTIFY_ADMIN_PASSWORD is missing a value" in first_line
    assert "Untrusted Compose diagnostic (tail):" in record["error"]
    assert "ODS turned this extension off" in record["error"]
    # The definition no longer participates in the merged Compose project,
    # and nothing else was deleted.
    assert not (extension / "compose.yaml").exists()
    assert (extension / "compose.yaml.disabled").read_text(encoding="utf-8") == GOTIFY_COMPOSE
    assert (extension / "manifest.yaml").is_file()
    assert not (host.install_dir / ".compose-flags").exists()
    assert _started(calls) == []


def test_compose_diagnostic_names_setting_even_when_redaction_rewrites_the_sentence(host):
    host.extension("bookstack")
    host.docker(config_error=BOOKSTACK_CONFIG_ERROR)

    host.install("bookstack")

    error = host.progress("bookstack")["error"]
    assert "Missing required setting: BOOKSTACK_DB_PASSWORD." in error.splitlines()[0]
    assert "BOOKSTACK_DB_PASSWORD is missing a value" in error


def test_compose_diagnostic_is_redacted_and_never_reports_resolved_configuration(host, monkeypatch):
    monkeypatch.setenv("BUILD_TEST_TOKEN", "process-credential")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return types.SimpleNamespace(
            returncode=1, stdout='{"services": {"x": {"environment": {"K": "resolved-credential"}}}}',
            stderr="failed near persisted-credential and process-credential\n" + GOTIFY_CONFIG_ERROR)
    monkeypatch.setattr(_mod.subprocess, "run", run)

    ok, error = _mod._prepare_install_images(["-p", "ods"], "gotify")

    assert ok is False
    assert calls == [["docker", "compose", "-p", "ods", "config", "--format", "json"]]
    for secret in ("persisted-credential", "process-credential", "resolved-credential"):
        assert secret not in error
    assert "Untrusted Compose error: error while interpolating" in error


def test_failed_source_build_is_disabled_too(host):
    extension = host.extension("gotify")
    calls = host.docker(build_error="failed to solve: exit code: 1")

    host.install("gotify")

    record = host.progress("gotify")
    assert record["error"].startswith("Source image build failed; containers were not started.")
    assert "ODS turned this extension off" in record["error"]
    assert (extension / "compose.yaml.disabled").is_file() and not (extension / "compose.yaml").exists()
    assert _started(calls) == []


def test_failed_setup_hook_is_disabled_and_keeps_its_error(host, monkeypatch):
    extension = host.extension("hooked")
    host.docker()
    monkeypatch.setattr(_mod, "_run_post_install_hook", lambda sid, ext_dir: (False, "openssl: not found"))

    host.install("hooked", run_setup_hook=True)

    record = host.progress("hooked")
    assert record["status"] == "error" and record["phase_label"] == "Setup failed"
    assert record["error"].startswith("openssl: not found\nODS turned this extension off")
    assert (extension / "compose.yaml.disabled").is_file() and not (extension / "compose.yaml").exists()


def test_start_failure_after_preparation_keeps_the_definition_enabled(host):
    # Compose resolved and images are ready: the definition is valid, so it
    # does not poison the project. Keep the existing behavior (logs, Remove).
    extension = host.extension("gotify")
    host.docker(up_error="port is already allocated")

    host.install("gotify")

    assert host.progress("gotify")["error"] == "port is already allocated"
    assert (extension / "compose.yaml").is_file()
    assert not (extension / "compose.yaml.disabled").exists()


def test_existing_disabled_copy_is_never_overwritten(host):
    extension = host.extension("gotify")
    (extension / "compose.yaml.disabled").write_text("owner copy\n", encoding="utf-8")
    host.docker(config_error=GOTIFY_CONFIG_ERROR)

    host.install("gotify")

    error = host.progress("gotify")["error"]
    assert "could not turn this extension off automatically" in error
    assert (extension / "compose.yaml").read_text(encoding="utf-8") == GOTIFY_COMPOSE
    assert (extension / "compose.yaml.disabled").read_text(encoding="utf-8") == "owner copy\n"


def test_builtin_extension_definition_is_not_renamed(host):
    extension = host.extension("builtin-svc", root=host.builtins)
    host.docker(config_error=GOTIFY_CONFIG_ERROR)

    host.install("builtin-svc")

    error = host.progress("builtin-svc")["error"]
    assert "GOTIFY_ADMIN_PASSWORD" in error and "turned this extension off" not in error
    assert (extension / "compose.yaml").is_file()


def test_enable_retry_of_unresolvable_library_extension_is_disabled_again(host, monkeypatch):
    extension = host.extension("gotify")
    host.docker(config_error=GOTIFY_CONFIG_ERROR)
    starts = []
    monkeypatch.setattr(_mod, "docker_compose_action", lambda sid, action: starts.append(sid) or (True, ""))

    _mod._enable_retry_work("gotify")

    record = host.progress("gotify")
    assert record["status"] == "error" and record["phase_label"] == "Retry failed"
    assert "Missing required setting: GOTIFY_ADMIN_PASSWORD." in record["error"]
    assert (extension / "compose.yaml.disabled").is_file() and not (extension / "compose.yaml").exists()
    assert starts == []


def test_enable_retry_of_resolvable_library_extension_starts(host, monkeypatch):
    extension = host.extension("gotify")
    host.docker()
    starts = []
    monkeypatch.setattr(_mod, "docker_compose_action", lambda sid, action: starts.append((sid, action)) or (True, ""))

    _mod._enable_retry_work("gotify")

    assert host.progress("gotify")["status"] == "started"
    assert starts == [("gotify", "start")]
    assert (extension / "compose.yaml").is_file()


def test_compose_resolution_timeout_is_not_treated_as_a_bad_definition(host, monkeypatch):
    # Docker may still be working after a CLI timeout; the install worker
    # records an uncertain operation and must not rewrite the definition.
    extension = host.extension("gotify")

    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 30))
    monkeypatch.setattr(_mod.subprocess, "run", run)

    host.install("gotify")

    assert host.progress("gotify")["status"] == "error"
    assert (extension / "compose.yaml").is_file()
