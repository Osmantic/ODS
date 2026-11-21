"""Engine ownership and stop/start sequencing, using an isolated Docker fixture."""

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux Compose lifecycle")


def entry(install, service, identity, running=True):
    return {
        "Id": identity, "Name": "/ods-" + service, "Image": "sha256:" + "e" * 64,
        "RestartCount": 0,
        "Config": {"Image": "old:pinned", "Labels": {
            "com.docker.compose.service": service,
            "com.docker.compose.project.working_dir": str(install),
            "com.docker.compose.project": "ods",
        }},
        "State": {"Running": running, "Paused": False, "Restarting": False, "Dead": False,
                  "Status": "running" if running else "exited", "Pid": 100 if running else 0,
                  "StartedAt": "2026-11-02T00:00:00Z"},
    }


class DockerFixture:
    def __init__(self, install):
        self.install = install
        self.rows = {"llama-server": None, "vllm-server": None}
        self.aliases = []
        self.calls = []
        self.mutations = []
        self.project = "ods"
        self.fail_stop = False
        self.fail_start = False
        self.keep_target = False
        self.generation = 0
        self.protected = {"name": "deepseek-protected", "id": "d" * 64, "running": True}

    def __call__(self, argv, timeout):
        self.calls.append((argv, timeout))
        assert argv[0] == "docker"
        if argv[1] == "ps":
            service = argv[5].split("=", 2)[-1]
            records = [row for row in [*self.rows.values(), *self.aliases]
                       if row and row["Config"]["Labels"]["com.docker.compose.service"] == service]
            return subprocess.CompletedProcess(argv, 0, "\n".join(row["Id"] for row in records), "")
        if argv[1] == "inspect":
            alias = next((row for row in self.aliases if row["Id"] == argv[-1]), None)
            if alias:
                return subprocess.CompletedProcess(argv, 0, json.dumps([alias]), "")
            service = argv[-1].removeprefix("ods-")
            assert service in self.rows  # Never inspect unrelated services.
            row = self.rows[service]
            if row is None:
                return subprocess.CompletedProcess(argv, 1, "", "Error: No such object: " + argv[-1])
            return subprocess.CompletedProcess(argv, 0, json.dumps([row]), "")
        if argv[1] == "stop":
            self.mutations.append(argv)
            assert argv[2:4] == ["--time", "30"]
            assert argv[-1] != self.protected["id"]
            if self.fail_stop:
                return subprocess.CompletedProcess(argv, 1, "", "fixture stop failure")
            row = next(row for row in self.rows.values() if row and row["Id"] == argv[-1])
            row["State"].update(Running=False, Pid=0, Status="exited")
        elif argv[1] == "compose" and argv[-3:] == ["config", "--format", "json"]:
            config = {"name": self.project, "services": {
                service: {"container_name": "ods-" + service, "image": "new:pinned"}
                for service in self.rows}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(config), "")
        elif argv[1] == "compose" and argv[-5:-1] == ["up", "-d", "--force-recreate", "--no-deps"]:
            self.mutations.append(argv)
            service = argv[-1]
            assert all(not row["State"]["Running"] for key, row in self.rows.items() if key != service and row)
            if not self.keep_target:
                self.generation += 1
                self.rows[service] = entry(self.install, service, f"{self.generation:064x}", not self.fail_start)
                self.rows[service]["Config"]["Image"] = "new:pinned"
            if self.fail_start:
                return subprocess.CompletedProcess(argv, 1, "", "fixture partial start failure")
        else:
            raise AssertionError(f"unexpected fixture command: {argv}")
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "bin"))
    import model_lifecycle_lock
    from model_switchboard import compose_runtime
    monkeypatch.delenv("INSTALL_USER", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setenv("ODS_MODEL_LIFECYCLE_LOCK_ROOT", str(tmp_path / "locks"))
    install = tmp_path / "install"
    install.mkdir()
    docker = DockerFixture(install)
    with model_lifecycle_lock.acquire(install) as lease:
        yield compose_runtime, docker, install, lease


def stage(case, service, expected=None):
    module, docker, install, lease = case
    return module.stage(install, ["-f", "compose.yml"], service, lease_fd=lease.fd,
                        run=docker, expected=expected)


@pytest.mark.parametrize("source,target", [
    ("llama-server", "vllm-server"), ("vllm-server", "llama-server"),
    ("llama-server", "llama-server"), ("vllm-server", "vllm-server"),
    (None, "vllm-server"),
])
def test_owned_engine_switch_has_no_overlap(case, source, target):
    module, docker, install, _ = case
    if source:
        docker.rows[source] = entry(install, source, "a" * 64)
    protected = copy.deepcopy(docker.protected)
    expected = module.capture(install, docker)
    receipt = stage(case, target, expected)
    assert receipt["previous"] == expected
    assert receipt["current"][target]["running"] is True
    assert sum(bool(row and row["running"]) for row in receipt["current"].values()) == 1
    assert docker.protected == protected
    stop_calls = [argv for argv in docker.mutations if argv[1] == "stop"]
    assert stop_calls == ([["docker", "stop", "--time", "30", "a" * 64]] if source and source != target else [])
    assert docker.mutations[-1][-5:] == ["up", "-d", "--force-recreate", "--no-deps", target]


@pytest.mark.parametrize("path,value", [
    (("Config", "Labels", "com.docker.compose.project.working_dir"), "/another/install"),
    (("Config", "Labels", "com.docker.compose.service"), "another-service"),
    (("State", "Paused"), True), (("State", "Restarting"), True),
    (("State", "Running"), False), (("State", "Pid"), True),
])
def test_invalid_or_protected_engine_refuses_without_mutation(case, path, value):
    _, docker, install, _ = case
    row = entry(install, "llama-server", "a" * 64)
    cursor = row
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    docker.rows["llama-server"] = row
    before = copy.deepcopy(docker.rows)
    with pytest.raises(RuntimeError):
        stage(case, "vllm-server")
    assert docker.rows == before
    assert docker.mutations == []


def test_both_running_refuses_without_choosing_a_victim(case):
    _, docker, install, _ = case
    for char, service in zip("ab", docker.rows):
        docker.rows[service] = entry(install, service, char * 64)
    with pytest.raises(RuntimeError, match="More than one"):
        stage(case, "vllm-server")
    assert not docker.mutations


@pytest.mark.parametrize("running", [False, True])
def test_renamed_owned_engine_is_preserved(case, running):
    _, docker, install, _ = case
    alias = entry(install, "llama-server", "a" * 64, running)
    alias["Name"] = "/preserved-engine"
    docker.aliases.append(alias)
    saved = copy.deepcopy(alias)
    with pytest.raises(RuntimeError, match="renamed or additional"):
        stage(case, "vllm-server")
    assert not docker.mutations and docker.aliases == [saved]


def test_other_installation_alias_is_not_stopped(case):
    _, docker, install, _ = case
    alias = entry(install.parent / "another-install", "llama-server", "d" * 64)
    alias["Name"] = "/other-installation-engine"
    alias["Config"]["Labels"]["com.docker.compose.project"] = "other_ods"
    docker.aliases.append(alias)
    saved = copy.deepcopy(alias)
    stage(case, "vllm-server")
    assert docker.aliases == [saved]
    assert all(argv[1] != "stop" for argv in docker.mutations)


def test_same_compose_project_cannot_adopt_foreign_renamed_engine(case):
    _, docker, install, _ = case
    alias = entry(install.parent / "another-install", "vllm-server", "d" * 64)
    alias["Name"] = "/foreign-project-member"
    docker.aliases.append(alias)
    with pytest.raises(RuntimeError, match="foreign engine"):
        stage(case, "vllm-server")
    assert not docker.mutations and alias["State"]["Running"]


def test_changed_captured_identity_refuses_before_stop(case):
    module, docker, install, _ = case
    docker.rows["llama-server"] = entry(install, "llama-server", "a" * 64)
    expected = module.capture(install, docker)
    docker.rows["llama-server"]["State"]["StartedAt"] = "2026-11-02T01:00:00Z"
    with pytest.raises(RuntimeError, match="identity changed"):
        stage(case, "vllm-server", expected)
    assert not docker.mutations


def test_project_change_refuses_before_stop(case):
    _, docker, install, _ = case
    docker.rows["llama-server"] = entry(install, "llama-server", "a" * 64)
    docker.project = "other"
    with pytest.raises(RuntimeError, match="project ownership"):
        stage(case, "vllm-server")
    assert not docker.mutations


def test_failed_stop_never_starts_target(case):
    _, docker, install, _ = case
    docker.rows["llama-server"] = entry(install, "llama-server", "a" * 64)
    docker.fail_stop = True
    with pytest.raises(RuntimeError, match="target was not started"):
        stage(case, "vllm-server")
    assert docker.rows["llama-server"]["State"]["Running"]
    assert docker.rows["vllm-server"] is None
    assert len(docker.mutations) == 1


def test_failed_start_does_not_claim_no_effect_or_readiness(case):
    _, docker, install, _ = case
    docker.rows["llama-server"] = entry(install, "llama-server", "a" * 64)
    docker.fail_start = True
    with pytest.raises(RuntimeError, match="recovery is required"):
        stage(case, "vllm-server")
    assert not docker.rows["llama-server"]["State"]["Running"]
    assert docker.rows["vllm-server"] is not None


def test_success_exit_without_recreation_is_not_accepted(case):
    _, docker, install, _ = case
    docker.rows["llama-server"] = entry(install, "llama-server", "a" * 64)
    docker.rows["llama-server"]["Config"]["Image"] = "new:pinned"
    docker.keep_target = True
    with pytest.raises(RuntimeError, match="generation"):
        stage(case, "llama-server")


def test_wrong_lease_refuses_even_reading_docker(case, tmp_path):
    module, docker, install, _ = case
    path = tmp_path / "unrelated"
    path.touch(mode=0o600)
    with path.open("rb") as stream:
        with pytest.raises(RuntimeError, match="canonical lock"):
            module.stage(install, ["-f", "compose.yml"], "vllm-server", lease_fd=stream.fileno(), run=docker)
    assert docker.calls == []
