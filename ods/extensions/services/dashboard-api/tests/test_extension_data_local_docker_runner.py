"""Pinned local Docker transport; never invoke a real daemon in these tests."""

# Imported pytest fixture is intentionally injected by name into the test.
# ruff: noqa: F811

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_data_local_docker_runner as local  # noqa: E402
from test_extension_data_docker_quiescence import FakeDocker  # noqa: E402
from test_extension_data_restore_journal import _ready  # noqa: E402
from test_extension_operation_lease_host_api import host_server  # noqa: E402, F401


linux_effect = pytest.mark.skipif(sys.platform != "linux", reason="Linux Docker transport")
_SOCKET = Path("/run/docker.sock")
_BINARY = Path("/usr/bin/docker")


def _mock_identity(monkeypatch):
    identities = {_SOCKET: (1, 2, 3, 4, 5), _BINARY: (6, 7, 8, 9, 10)}
    monkeypatch.setattr(local, "_identity", lambda path, *, socket: identities[path])
    return identities


@linux_effect
def test_only_local_read_queries_with_scrubbed_client_environment(monkeypatch):
    _mock_identity(monkeypatch)
    monkeypatch.setenv("DOCKER_CONTEXT", "remote-production")
    monkeypatch.setenv("DOCKER_HOST", "tcp://external:2375")
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        output = b'"daemon-stable"\n' if argv[-3:] == ["info", "--format", "{{json .ID}}"] else b""
        return subprocess.CompletedProcess(argv, 0, output, b"")

    monkeypatch.setattr(local.subprocess, "run", fake_run)
    runner = local.PinnedLocalDockerRunner()
    result = runner(["container", "ls", "--no-trunc", "--format", "{{.ID}}"])

    assert result.stdout == b""
    assert len(calls) == 4  # construction, before, query, after
    for argv, kwargs in calls:
        assert argv[:3] == [str(_BINARY), "--host", "unix:///run/docker.sock"]
        assert kwargs["env"] == {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 15


@linux_effect
@pytest.mark.parametrize("query", [
    ["container", "stop", "ods-alpha"],
    ["container", "ls", "--filter", "label=com.docker.compose.service=alpha"],
    ["container", "ls", "--all", "--filter",
     "label=com.docker.compose.service=alpha;rm", "--format", "{{.State}}"],
    ["inspect", "--type", "container", "--format", "{{json .Mounts}}", "not-an-id"],
    ["info", "--format", "{{json .ID}}"],
])
def test_refuses_commands_outside_observer_allowlist(monkeypatch, query):
    _mock_identity(monkeypatch)
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b'"daemon"\n', b"")

    monkeypatch.setattr(local.subprocess, "run", fake_run)
    runner = local.PinnedLocalDockerRunner()
    with pytest.raises(local.LocalDockerRunnerError):
        runner(query)
    assert len(calls) == 1


@linux_effect
@pytest.mark.parametrize("drift", ["daemon", "socket", "binary"])
def test_refuses_changed_daemon_or_socket_or_client(monkeypatch, drift):
    identities = _mock_identity(monkeypatch)
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if argv[-3:] == ["info", "--format", "{{json .ID}}"]:
            value = b'"daemon-new"\n' if drift == "daemon" and len(calls) > 1 else b'"daemon-old"\n'
        else:
            value = b""
        return subprocess.CompletedProcess(argv, 0, value, b"")

    monkeypatch.setattr(local.subprocess, "run", fake_run)
    runner = local.PinnedLocalDockerRunner()
    if drift == "socket":
        identities[_SOCKET] = (1, 999, 3, 4, 5)
    elif drift == "binary":
        identities[_BINARY] = (6, 999, 8, 9, 10)
    with pytest.raises(local.LocalDockerRunnerError) as caught:
        runner(["container", "ls", "--no-trunc", "--format", "{{.ID}}"])
    assert caught.value.code == "lifecycle-work-data-quiescence-docker-unavailable"
    assert len(calls) == (2 if drift == "daemon" else 1)


@linux_effect
def test_rejects_symlinked_socket_before_any_cli_call(tmp_path, monkeypatch):
    socket = tmp_path / "docker.sock"
    socket.symlink_to(tmp_path / "other.sock")
    calls = []
    monkeypatch.setattr(local.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(local.LocalDockerRunnerError):
        local.PinnedLocalDockerRunner(socket=socket)
    assert calls == []


@linux_effect
def test_host_default_uses_pinned_runner_not_request_selected_context(
    tmp_path, host_server, monkeypatch,
):
    agent, _listener = host_server
    install, _backup, _alpha, _store, command, _root, _journal = _ready(tmp_path)
    agent.INSTALL_DIR = install
    for service_id in command.service_ids:
        directory = agent.EXTENSIONS_DIR / service_id
        directory.mkdir()
        (directory / "manifest.yaml").write_text("service: {}\n", encoding="utf-8")
    manager = agent._get_extension_lease_manager()
    grant = manager.acquire(command.transaction_id, command.plan_hash, command.service_ids)
    evidence = agent._ExtensionMutationLeaseEvidence(
        grant["leaseId"], grant["leaseToken"], command.transaction_id, command.plan_hash,
    )
    admission = agent._ExtensionMutationAdmission(None, evidence, command.service_ids)
    fake = FakeDocker(states={item: b"exited\n" for item in command.service_ids})
    builds = []
    monkeypatch.setattr(local, "PinnedLocalDockerRunner", lambda: builds.append(True) or fake)
    with admission:
        observer = admission.docker_restore_observer(command)
        assert observer() is True
    assert builds == [True]
    assert fake.calls
