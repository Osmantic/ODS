"""Real unprivileged PTYs, temporary fixed helpers; never calls host sudo."""

import importlib.util
import os
from pathlib import Path
import time
import sys
import subprocess
import signal
import threading
from types import SimpleNamespace

import pytest

if sys.platform != "linux":
    pytest.skip("Linux PTY only", allow_module_level=True)

SOURCE = Path(__file__).parents[1] / "host" / "approval_terminal.py"
spec = importlib.util.spec_from_file_location("approval_terminal", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
pytestmark = pytest.mark.skipif(
    os.name != "posix" or os.getuid() == 0, reason="nonroot Linux PTY required"
)
JOB = "ops-1790800000000-" + "a" * 12
PLAN = "b" * 64


def child_module_without_host_sudo(tmp_path):
    target = tmp_path / "approval_terminal_fixture.py"
    source = SOURCE.read_text()
    original = "        _revoke_credentials()\n"
    assert source.count(original) == 1
    target.write_text(
        source.replace(original, "        pass  # fixture: no host sudo\n")
    )
    return target


@pytest.fixture
def make(tmp_path, monkeypatch):
    # Explicitly prevent credential/cache operations on the developer host.
    revoked = []

    def revoke(self):
        revoked.append(True)
        self._cleanup = True

    monkeypatch.setattr(module.ApprovalPTY, "_revoke", revoke)
    monkeypatch.setattr(
        module, "__file__", str(child_module_without_host_sudo(tmp_path))
    )
    processes = []

    def start(body, **kwargs):
        helper = tmp_path / "bin" / "ods-pixel-approve"
        helper.parent.mkdir(exist_ok=True)
        helper.write_text("#!/usr/bin/python3\n" + body)
        helper.chmod(0o700)
        terminal = module.ApprovalPTY(tmp_path, JOB, PLAN, **kwargs)
        processes.append(terminal)
        return terminal

    yield start, revoked
    for terminal in processes:
        terminal.cancel()


def until(terminal, text=None):
    output = ""
    cursor = 0
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        value = terminal.poll(cursor)
        cursor = value["nextCursor"]
        output += value["output"]
        if (
            text is not None
            and text in output
            or text is None
            and value["exitCode"] is not None
        ):
            return value, output
        time.sleep(0.02)
    pytest.fail("bounded PTY fixture did not finish")


def gone_or_zombie(proc):
    # The child can be reaped between exists() and read_text(). Read once;
    # only an absent proc entry or an observed zombie satisfies cleanup.
    try:
        return proc.read_text().split()[2] == "Z"
    except FileNotFoundError:
        return True


def test_reaped_proc_during_observation_is_gone(monkeypatch):
    proc = Path("/proc/12345/stat")
    monkeypatch.setattr(Path, "exists", lambda _self: True)

    def reaped(_self):
        raise FileNotFoundError("fixture: reaped before read")

    monkeypatch.setattr(Path, "read_text", reaped)
    assert gone_or_zombie(proc)


@pytest.mark.parametrize("state", ["R", "S", "D", "T", "Z"])
def test_proc_observation_accepts_only_zombie_state(tmp_path, state):
    proc = tmp_path / "stat"
    proc.write_text(f"12345 (fixture) {state} 1 1 1\n")
    assert gone_or_zombie(proc) is (state == "Z")


def test_proc_observation_does_not_hide_permission_errors(monkeypatch):
    def denied(_self):
        raise PermissionError("fixture: unreadable proc entry")

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(PermissionError):
        gone_or_zombie(Path("/proc/12345/stat"))


def test_real_controlling_tty_fixed_arguments_and_no_password_echo(make):
    start, revoked = make
    terminal = start(
        "import os,sys\nassert all(os.isatty(i) for i in (0,1,2))\nassert os.open('/dev/tty',os.O_RDWR)>=0\nassert sys.argv[1:]=="
        + repr([JOB, PLAN, "--confirm"])
        + "\nprint('Password:',flush=True)\nassert input()=='never-log-this-secret'\nprint('accepted',flush=True)\n"
    )
    until(terminal, "Password:")
    terminal.input(0, "never-log-this-secret")
    value, output = until(terminal)
    assert value["exitCode"] == 0
    assert "never-log-this-secret" not in output
    assert "accepted" in output
    terminal.cancel()
    assert revoked == [True]


def test_sequenced_input_no_control_characters_no_replay(make):
    start, _ = make
    terminal = start("import time\nprint('ready',flush=True)\ntime.sleep(5)\n")
    until(terminal, "ready")
    for seq, line in [(True, "x"), (0, "a\nb"), (0, "\x03"), (0, "a" * 1025)]:
        with pytest.raises(module.ApprovalTerminalError):
            terminal.input(seq, line)
    terminal.input(0, "one")
    with pytest.raises(module.ApprovalTerminalError):
        terminal.input(0, "replayed")
    assert terminal.cancel() == {"stopped": True, "cleanupConfirmed": True}


def test_cancel_and_deadline_drain_private_process(make):
    start, _ = make
    terminal = start(
        "import time\nprint('ready',flush=True)\ntime.sleep(10)\n", timeout=0.2
    )
    value, _ = until(terminal)
    assert value["state"] == "timed-out"
    assert terminal.cancel()["stopped"]


def test_output_budget_aborts_instead_of_dropping_plan(make):
    start, _ = make
    terminal = start("import os,time\nos.write(1,b'x'*1200000)\ntime.sleep(10)\n")
    time.sleep(0.4)
    value, _ = until(terminal)
    assert value["state"] == "output-limit"


def test_host_paths_and_model_commands_are_not_input_schema(tmp_path):
    for job, plan in [(";id", PLAN), (JOB, "../secret"), (JOB, True)]:
        with pytest.raises(module.ApprovalTerminalError):
            module.ApprovalPTY(tmp_path, job, plan)


def test_stop_never_signals_a_reaped_leader_pid(monkeypatch):
    terminal = object.__new__(module.ApprovalPTY)
    terminal._lock = threading.RLock()
    terminal._state = "running"
    terminal._process = SimpleNamespace(pid=12345, poll=lambda: 0)
    monkeypatch.setattr(
        module.os, "killpg", lambda *_: pytest.fail("reaped PID signalled")
    )
    terminal._stop("exited")


def test_output_cursor_survives_lost_reply_without_missing_plan(make):
    start, _ = make
    terminal = start(
        "import time\nprint('complete protected plan',flush=True)\ntime.sleep(1)\n"
    )
    until(terminal, "protected plan")
    first = terminal.poll(0)
    assert terminal.poll(0)["output"] == first["output"]
    assert terminal.poll(first["nextCursor"])["output"] == ""
    with pytest.raises(module.ApprovalTerminalError):
        terminal.poll(first["nextCursor"] + 1)


def test_output_cursor_keeps_multibyte_protected_plan_intact(make):
    start, _ = make
    plan_text = "€" * 23000
    terminal = start("import os\nos.write(1,bytes([226,130,172])*23000)\n")
    value, output = until(terminal)
    while value["moreOutput"]:
        value = terminal.poll(value["nextCursor"])
        output += value["output"]
    assert output == plan_text


def test_cancellation_kills_descendant_holding_tty(make, tmp_path):
    start, _ = make
    pid_file = tmp_path / "child.pid"
    terminal = start(
        "import os,time\npid=os.fork()\nif pid==0:\n while True: time.sleep(1)\nopen("
        + repr(str(pid_file))
        + ", 'w').write(str(pid))\nprint('ready',flush=True)\nwhile True: time.sleep(1)\n"
    )
    until(terminal, "ready")
    child = int(pid_file.read_text())
    assert terminal.cancel()["stopped"] is True
    proc = Path(f"/proc/{child}/stat")
    assert gone_or_zombie(proc)


def test_registry_rejects_foreign_owner_unknown_fields_and_unapproved_plan(tmp_path):
    owner = "d" * 64
    manager = module.ApprovalTerminals(tmp_path, lambda job, plan: False)
    with pytest.raises(module.ApprovalTerminalError, match="plan-not-awaiting"):
        manager.request({"action": "start", "owner": owner, "job": JOB, "plan": PLAN})
    for body in [
        {"action": "start", "owner": owner, "job": JOB, "plan": PLAN, "command": "id"},
        {"action": "poll", "owner": owner, "session": "e" * 64, "cursor": 0},
    ]:
        with pytest.raises(module.ApprovalTerminalError):
            manager.request(body)


def test_registry_binds_active_handle_to_browser_owner(make, tmp_path, monkeypatch):
    start, _ = make
    terminal = start("import time\nprint('ready',flush=True)\ntime.sleep(5)\n")
    until(terminal, "ready")
    monkeypatch.setattr(module, "ApprovalPTY", lambda *_args: terminal)
    manager = module.ApprovalTerminals(
        tmp_path, lambda job, plan: (job, plan) == (JOB, PLAN)
    )
    owner = "d" * 64
    receipt = manager.request(
        {"action": "start", "owner": owner, "job": JOB, "plan": PLAN}
    )
    for action, extra in [
        ("poll", {"cursor": 0}),
        ("input", {"sequence": 0, "line": "denied"}),
        ("cancel", {}),
    ]:
        with pytest.raises(
            module.ApprovalTerminalError, match="terminal-session-unavailable"
        ):
            manager.request(
                {
                    "action": action,
                    "owner": "e" * 64,
                    "session": receipt["session"],
                    **extra,
                }
            )
    value = manager.request(
        {"action": "poll", "owner": owner, "session": receipt["session"], "cursor": 0}
    )
    assert value["state"] == "running" and value["nextSequence"] == 0


def test_host_death_closes_own_tty_without_leaving_helper(tmp_path):
    helper = tmp_path / "bin" / "ods-pixel-approve"
    helper.parent.mkdir()
    child_file = tmp_path / "helper.pid"
    helper.write_text(
        "#!/usr/bin/python3\nimport os,time\nopen("
        + repr(str(child_file))
        + ",'w').write(str(os.getpid()))\nwhile True: time.sleep(1)\n"
    )
    helper.chmod(0o700)
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import importlib.util,time\ns=importlib.util.spec_from_file_location('qa',"
        + repr(str(child_module_without_host_sudo(tmp_path)))
        + ")\nm=importlib.util.module_from_spec(s)\ns.loader.exec_module(m)\n"
        "m.ApprovalPTY._revoke=lambda self: None\nt=m.ApprovalPTY("
        + repr(str(tmp_path))
        + ","
        + repr(JOB)
        + ","
        + repr(PLAN)
        + ")\ntime.sleep(30)\n"
    )
    process = subprocess.Popen([sys.executable, str(driver)])
    child = None
    try:
        deadline = time.monotonic() + 4
        while not child_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        child = int(child_file.read_text())
        process.kill()
        process.wait(timeout=3)
        proc = Path(f"/proc/{child}/stat")
        deadline = time.monotonic() + 3
        while not gone_or_zombie(proc) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert gone_or_zombie(proc)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        if child:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
