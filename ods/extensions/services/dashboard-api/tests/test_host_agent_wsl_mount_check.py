"""After a WSL VM restart, the host agent repairs containers that lost their data.

On an RTX Spark (2026-10-25) Windows ran out of commit, WSL crashed and
restarted, and Docker Desktop brought ODS's containers back with an EMPTY
folder where ODS's data belongs: /data was empty in ods-dashboard-api while
~/ods/data was full. These tests drive the agent's check with Docker, ods-cli
and the clock faked: `ods repair mounts --check`, then `ods repair mounts`
under the model lifecycle and every service lock, at most twice per WSL boot,
and a bounded wait for a Docker Desktop restart when a new container could not
see the data either. Nothing here starts Docker, bash or WSL.
"""

import json
import logging
import subprocess

import pytest

from test_host_agent_install_rollback import _mod

BOOT = "11111111-2222-3333-4444-555555555555"
NEXT_BOOT = "66666666-7777-8888-9999-000000000000"
STACK = (("ods-dashboard-api", "2026-10-25T14:12:01Z", "running"),
         ("ods-webui", "2026-10-25T14:12:03Z", "running"))
RESTARTED = (("ods-dashboard-api", "2026-10-25T15:02:40Z", "running"),
             ("ods-webui", "2026-10-25T15:02:41Z", "running"))


class _Host:
    """Docker's view of the stack, ods-cli's answers and the clock."""

    def __init__(self, tmp_path, monkeypatch, cli, stacks=None):
        self.data = tmp_path / "data"
        self.data.mkdir(parents=True)
        self.record = self.data / "wsl-self-heal.json"
        self.cli = list(cli)       # (code, detail) answers, in order
        self.calls: list[tuple] = []
        self.stacks = list(stacks or [STACK])
        self.now = 0.0
        monkeypatch.setattr(_mod, "DATA_DIR", self.data)
        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(_mod, "_ods_stack_fingerprint", self.fingerprint)
        monkeypatch.setattr(_mod, "_ods_repair_mounts", self.repair)
        monkeypatch.setattr(_mod, "_declared_docker_containers", lambda: {
            "ods-dashboard-api": "dashboard-api", "ods-webui": "open-webui", "ods-llama-server": "llama-server",
        })
        monkeypatch.setattr(_mod, "_pixel_model_transition_pending_for_route_proof", lambda: False)

    def fingerprint(self):
        # The last answer repeats: the stack stays as it was.
        return self.stacks.pop(0) if len(self.stacks) > 1 else self.stacks[0]

    def repair(self, check):
        on_disk = json.loads(self.record.read_text()) if self.record.exists() else {}
        self.calls.append(("check" if check else "repair", on_disk.get("state"), on_disk.get("repairs"),
                           _mod._model_lifecycle_status().get("activeOperation"),
                           _mod._service_locks["dashboard-api"].locked()))
        return self.cli.pop(0)

    def sleep(self, seconds):
        self.now += seconds

    def heal(self, boot=BOOT, **kwargs):
        return _mod._wsl_bind_self_heal(boot, self.record, clock=lambda: self.now, sleep=self.sleep, **kwargs)

    def saved(self):
        return json.loads(self.record.read_text())


def _lifecycle_free():
    return _mod._model_lifecycle_status().get("lifecycleActive") is None


def test_containers_that_see_their_data_are_left_alone(tmp_path, monkeypatch):
    host = _Host(tmp_path, monkeypatch, cli=[(0, "all bind views healthy")])

    assert host.heal() == "healthy"
    # One read-only check, outside every lock; nothing is recreated.
    assert host.calls == [("check", None, None, None, False)]
    assert host.saved()["state"] == "healthy" and host.saved()["bootId"] == BOOT
    assert not host.saved()["next"]


def test_stale_data_is_repaired_under_every_lock_after_recording_the_attempt(tmp_path, monkeypatch):
    host = _Host(tmp_path, monkeypatch, cli=[
        (2, "service dashboard-api (ods-dashboard-api) stale: stale-bind:/data"),
        (0, "verified dashboard-api"),
    ])

    assert host.heal() == "repaired"
    # The repair runs holding the model lifecycle and the service locks, and
    # only after the record already counts it for this boot.
    assert host.calls == [
        ("check", None, None, None, False),
        ("repair", "repairing", 1, "wsl_bind_recovery", True),
    ]
    assert host.saved()["state"] == "repaired" and host.saved()["repairs"] == 1
    assert _lifecycle_free() and not _mod._service_locks["dashboard-api"].locked()


def test_no_proof_means_nothing_changed_and_the_next_step_is_restarting_docker_desktop(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="ods-host-agent")
    host = _Host(tmp_path, monkeypatch, cli=[(2, "stale"), (3, "a new container does not see /home/u/ods/data")])

    assert _mod._wsl_bind_self_heal(BOOT, host.record, clock=lambda: host.now, sleep=host.sleep) == \
        "needs-docker-desktop-restart"
    saved = host.saved()
    assert saved["state"] == "needs-docker-desktop-restart" and saved["repairs"] == 1
    assert "Restart Docker Desktop" in saved["next"] and "ods repair mounts" in saved["next"]
    _mod._log_wsl_self_heal("needs-docker-desktop-restart")
    assert "Restart Docker Desktop" in caplog.text


@pytest.mark.parametrize("busy", ["service", "model", "pixel"])
def test_busy_operations_defer_the_repair_to_the_next_poll(tmp_path, monkeypatch, busy):
    host = _Host(tmp_path, monkeypatch, cli=[(2, "stale"), (2, "stale"), (0, "verified")])
    release = None
    if busy == "service":
        lock = _mod._service_locks["open-webui"]
        assert lock.acquire(blocking=False)
        release = lock.release
    elif busy == "model":
        assert _mod._begin_model_lifecycle("model_activation", "qwen")[0]
        release = lambda: _mod._end_model_lifecycle("model_activation")  # noqa: E731
    else:
        pending = iter([True])
        monkeypatch.setattr(_mod, "_pixel_model_transition_pending_for_route_proof",
                            lambda: next(pending, False))

    sleeps = []

    def sleep(seconds):
        # The first sleep is the wait for the stack to settle; the other
        # operation finishes during the second, after one deferred attempt.
        nonlocal release
        sleeps.append(seconds)
        host.now += seconds
        if release is not None and len(sleeps) == 2:
            release()
            release = None

    try:
        state = _mod._wsl_bind_self_heal(BOOT, host.record, clock=lambda: host.now, sleep=sleep)
    finally:
        if busy == "service" and _mod._service_locks["open-webui"].locked():
            _mod._service_locks["open-webui"].release()
        if busy == "model" and _mod._model_lifecycle_status().get("activeOperation") == "model_activation":
            _mod._end_model_lifecycle("model_activation")

    assert state == "repaired"
    # Deferred once without a repair attempt or a count, then repaired.
    assert [call[0] for call in host.calls] == ["check", "check", "repair"]
    assert host.saved()["repairs"] == 1
    assert _lifecycle_free()


def test_at_most_two_repairs_per_wsl_boot_and_a_new_boot_starts_over(tmp_path, monkeypatch):
    host = _Host(tmp_path, monkeypatch, cli=[(2, "stale"), (2, "stale"), (3, "no proof")])
    host.record.write_text(json.dumps({"bootId": BOOT, "state": "needs-docker-desktop-restart", "repairs": 2}))

    assert host.heal() == "repair-limit"
    assert host.calls == [("check", "needs-docker-desktop-restart", 2, None, False)]
    assert "ods repair mounts" in host.saved()["next"]

    # Another WSL boot: the old record does not count.
    assert host.heal(NEXT_BOOT) == "needs-docker-desktop-restart"
    assert host.saved()["bootId"] == NEXT_BOOT and host.saved()["repairs"] == 1


def test_a_repair_that_finds_another_one_running_does_not_count(tmp_path, monkeypatch):
    host = _Host(tmp_path, monkeypatch, cli=[(2, "stale"), (4, "another ODS mount recovery is still running"),
                                             (0, "all bind views healthy")])

    assert host.heal() == "healthy"
    assert [call[:3] for call in host.calls] == [
        ("check", None, None), ("repair", "repairing", 1), ("check", "checking", 0)]
    assert host.saved()["repairs"] == 0


def test_the_stack_must_settle_before_the_check_and_the_wait_is_bounded(tmp_path, monkeypatch):
    starting = (("ods-dashboard-api", "", "created"),)
    host = _Host(tmp_path, monkeypatch, cli=[(0, "healthy")], stacks=[None, starting, STACK, STACK])

    assert host.heal() == "healthy"
    # Docker silent, then still starting, then the same stack twice in a row.
    assert host.now == 3 * _mod._WSL_BIND_CHECK_POLL_SECONDS

    silent = _Host(tmp_path / "silent", monkeypatch, cli=[], stacks=[None])
    assert silent.heal() == "docker-unavailable"
    assert silent.calls == []
    assert silent.now <= _mod._WSL_BIND_CHECK_WINDOW_SECONDS
    assert "Start Docker Desktop" in silent.saved()["next"]

    empty = _Host(tmp_path / "empty", monkeypatch, cli=[], stacks=[()])
    assert empty.heal() == "no-containers" and empty.calls == []


@pytest.mark.parametrize("code,state", [(1, "check-failed"), (-1, "check-failed")])
def test_a_check_that_cannot_run_is_reported_not_repaired(tmp_path, monkeypatch, code, state):
    host = _Host(tmp_path, monkeypatch, cli=[(code, "compose config failed")])
    assert host.heal() == state
    assert host.calls == [("check", None, None, None, False)]
    assert host.saved()["detail"] == "compose config failed"


def test_a_settled_boot_is_not_checked_again_by_a_later_agent_start(tmp_path, monkeypatch):
    host = _Host(tmp_path, monkeypatch, cli=[])
    host.record.write_text(json.dumps({"bootId": BOOT, "state": "repaired", "repairs": 1}))
    assert _mod._run_wsl_bind_self_heal(BOOT) == "settled"
    assert host.calls == []
    assert _mod._run_wsl_bind_self_heal(None) == "skipped"


def test_a_docker_desktop_restart_gets_one_more_repair(tmp_path, monkeypatch):
    host = _Host(tmp_path, monkeypatch, cli=[(2, "stale"), (0, "verified")],
                 stacks=[STACK, STACK, RESTARTED, RESTARTED])
    host.record.write_text(json.dumps({"bootId": BOOT, "state": "needs-docker-desktop-restart", "repairs": 1}))

    assert _mod._watch_for_docker_restart(BOOT, clock=lambda: host.now, sleep=host.sleep) == "repaired"
    assert [call[:3] for call in host.calls] == [("check", "needs-docker-desktop-restart", 1),
                                                 ("repair", "repairing", 2)]


def test_without_a_docker_restart_the_watch_ends_quietly(tmp_path, monkeypatch):
    host = _Host(tmp_path, monkeypatch, cli=[], stacks=[STACK])
    assert _mod._watch_for_docker_restart(BOOT, clock=lambda: host.now, sleep=host.sleep) is None
    assert host.calls == []
    assert host.now <= _mod._WSL_BIND_WATCH_SECONDS


def test_the_thread_repairs_mounts_then_checks_ports_then_watches_only_when_needed(tmp_path, monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(_mod, "_linux_boot_id", lambda: BOOT)

    def ports():
        events.append("ports")
        return {"ods-webui": "still-dead", "ods-whisper": "repaired"}

    def watch(boot):
        events.append("watch:" + boot)

    monkeypatch.setattr(_mod, "_run_wsl_port_check", ports)
    monkeypatch.setattr(_mod, "_watch_for_docker_restart", watch)

    for state, expected in (("healthy", ["mounts", "ports"]), ("settled", ["mounts", "ports"]),
                            ("needs-docker-desktop-restart", ["mounts", "ports", "watch:" + BOOT]),
                            ("docker-unavailable", ["mounts", "ports", "watch:" + BOOT])):
        events.clear()

        def mounts(_boot, result=state):
            events.append("mounts")
            return result

        monkeypatch.setattr(_mod, "_run_wsl_bind_self_heal", mounts)
        _mod._run_wsl_restart_recovery()
        assert events == expected

    record = json.loads((tmp_path / "wsl-self-heal.json").read_text())
    assert record["deadPorts"] == ["ods-webui"] and "Restart Docker Desktop" in record["portsNext"]


def test_the_repair_runs_ods_cli_from_the_installation(tmp_path, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 2, "", "line one\n\nline two\n")

    monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(_mod, "_find_usable_bash", lambda: "/usr/bin/bash")
    monkeypatch.setattr(_mod.subprocess, "run", run)

    assert _mod._ods_repair_mounts(check=True) == (2, "line one | line two")
    argv, kwargs = calls[0]
    assert argv == ["/usr/bin/bash", str(tmp_path / "ods-cli"), "repair", "mounts", "--check"]
    assert kwargs["cwd"] == str(tmp_path) and kwargs["timeout"] == _mod._WSL_BIND_CLI_TIMEOUT_SECONDS
    assert kwargs["env"]["INSTALL_DIR"] == str(tmp_path) and kwargs["env"]["ODS_HOME"] == str(tmp_path)
    _mod._ods_repair_mounts(check=False)
    assert calls[1][0][-1] == "mounts"

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(_mod.subprocess, "run", timeout)
    assert _mod._ods_repair_mounts(check=False)[0] == -1


def test_the_stack_fingerprint_is_this_installations_containers_only(tmp_path, monkeypatch):
    install = tmp_path / "ods"
    install.mkdir()
    monkeypatch.setattr(_mod, "INSTALL_DIR", install)

    def row(name, working_dir, started="2026-10-25T14:12:01Z"):
        return {"Name": "/" + name, "State": {"StartedAt": started, "Status": "running"},
                "Config": {"Labels": {"com.docker.compose.project": "ods",
                                      "com.docker.compose.project.working_dir": str(working_dir)}}}

    rows = [row("ods-webui", install), row("ods-dashboard-api", install, "2026-10-25T14:12:00Z"),
            row("ods-other-install", tmp_path / "elsewhere")]
    answers = {"ps": subprocess.CompletedProcess([], 0, "a\nb\nc\n", ""),
               "inspect": subprocess.CompletedProcess([], 0, json.dumps(rows), "")}

    def run(argv, **_kwargs):
        assert argv[:2] in (["docker", "ps"], ["docker", "inspect"]), argv
        if argv[1] == "ps":
            assert "--all" in argv and "label=com.docker.compose.project=ods" in argv
        return answers[argv[1]]

    monkeypatch.setattr(_mod.subprocess, "run", run)
    assert _mod._ods_stack_fingerprint() == (
        ("ods-dashboard-api", "2026-10-25T14:12:00Z", "running"),
        ("ods-webui", "2026-10-25T14:12:01Z", "running"),
    )
    answers["ps"] = subprocess.CompletedProcess([], 0, "", "")
    assert _mod._ods_stack_fingerprint() == ()
    answers["ps"] = subprocess.CompletedProcess([], 1, "", "Cannot connect to the Docker daemon")
    assert _mod._ods_stack_fingerprint() is None
