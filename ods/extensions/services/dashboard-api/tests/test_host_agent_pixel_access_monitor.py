"""The host agent keeps Portal's access proof current instead of giving up.

On the RTX Spark test laptop (2026-10-25) the agent started at 15:13:06 and,
at 15:15:31, logged "Pixel startup reproof requires attention
(stage=unsafe-state reason=transition-recovery-required)" and "Pixel access
monitor stopped". A model hold held the coordinator at boot. When a later
gateway restart dropped the runtime proof, nothing re-proved it and Portal
stayed unverified until someone clicked Verify Sandbox in Settings.

These tests drive the real monitor, its checks and the access-mode status
handler with the protected helper, the coordinator socket and the clock
faked. Nothing here starts Docker, bash, WSL or a helper.
"""

import io
import json
import logging
import stat
import subprocess
import threading
import types

import pytest

# Loading the agent puts ods/bin, where these two modules live, on sys.path.
from test_host_agent_install_rollback import _mod
import pixel_access_client
import pixel_access_relay

ATTENTION = ("attention", "stage=unsafe-state reason=transition-recovery-required")
STALE_SANDBOX = {"available": True, "surface": "wsl-systemd", "configured_mode": "sandboxed",
                 "effective_mode": "unknown", "runtime_verified": False, "revision": "a" * 64,
                 "busy": False, "pending": False, "reason": "runtime-proof-required", "scope": "owner-host"}
VERIFIED = {**STALE_SANDBOX, "effective_mode": "sandboxed", "runtime_verified": True, "reason": None}


class Stop(Exception):
    """Ends the otherwise endless monitor loop after the scripted checks."""


@pytest.fixture(autouse=True)
def _quiet_wake():
    _mod._pixel_access_monitor_wake.clear()
    yield
    _mod._pixel_access_monitor_wake.clear()


def _run_monitor(monkeypatch, checks, *, hold=(), verified=()):
    """Run the real monitor over scripted checks.

    Returns each wait it asked for and, in order, which check ran: the
    protected helper ("helper"), the read-only status ("status") or the
    model journal ("journal").
    """
    waits: list[tuple] = []
    ran: list[str] = []
    check = iter([*checks, Stop()])
    holds = iter(hold)
    proofs = iter(verified)

    def reconcile():
        ran.append("helper")
        outcome = next(check)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def hold_pending():
        ran.append("journal")
        return next(holds, False)

    def access_verified():
        ran.append("status")
        return next(proofs)

    monkeypatch.setattr(_mod, "_reconcile_native_pixel_startup", reconcile)
    monkeypatch.setattr(_mod, "_pixel_model_hold_pending", hold_pending)
    monkeypatch.setattr(_mod, "_pixel_access_verified", access_verified)
    monkeypatch.setattr(_mod, "_wait_for_pixel_access_check",
                        lambda delay, *, wake: waits.append((delay, wake)))
    with pytest.raises(Stop):
        _mod._monitor_native_pixel_access()
    return waits, ran


def test_a_boot_time_hold_no_longer_stops_the_monitor(monkeypatch, caplog):
    # The laptop's sequence: refused at boot, still refused, then the hold
    # resolved and the next restart's stale proof was re-proved on schedule.
    checks = [ATTENTION, ATTENTION, ("recheck", None), ("recheck", None)]
    with caplog.at_level(logging.INFO, logger="ods-host-agent"):
        waits, ran = _run_monitor(monkeypatch, checks)
    assert waits == [(30, True), (60, True), (30, True), (30, True)]
    assert ran.count("helper") == 5
    messages = [record.getMessage() for record in caplog.records]
    assert messages.count("Pixel startup reproof requires attention (stage=unsafe-state "
                          "reason=transition-recovery-required); the access monitor keeps checking") == 1
    assert "Pixel access monitor resumed automatic reproof" in messages
    assert not any("stopped" in message for message in messages)


def test_backoff_is_bounded_and_resets_when_the_state_changes(monkeypatch):
    chat = ("attention", "stage=unsafe-state reason=chat-recovery-required")
    waits, _ran = _run_monitor(monkeypatch, [*[ATTENTION] * 7, chat, chat])
    assert [delay for delay, _wake in waits] == [30, 60, 120, 240, 300, 300, 300, 30, 60]


def test_an_unconfirmed_change_is_never_retried_until_status_is_verified(monkeypatch, caplog):
    uncertain = ("uncertain", "stage=change-failed reason=None")
    with caplog.at_level(logging.WARNING, logger="ods-host-agent"):
        waits, ran = _run_monitor(monkeypatch, [uncertain, ("recheck", None)], verified=[False, False, True])
    # While latched only the read-only status ran, and no wake could cut a wait.
    assert ran == ["journal", "helper", "status", "status", "status",
                   "journal", "helper", "journal", "helper"]
    assert waits == [(30, False), (60, False), (120, False), (30, True), (30, True)]
    assert [record.getMessage() for record in caplog.records] == [
        "Pixel access change was not confirmed (stage=change-failed reason=None); no automatic retry. "
        "Reading status until the runtime is verified again"]


def test_an_open_model_transaction_keeps_the_monitor_off_the_lifecycle_lock(monkeypatch):
    begun: list[tuple] = []

    def begin(*args):
        begun.append(args)
        return True, {}

    monkeypatch.setattr(_mod, "_begin_model_lifecycle", begin)
    waits, ran = _run_monitor(monkeypatch, [("recheck", None)], hold=[True, True, True])
    assert begun == []
    assert ran == ["journal", "journal", "journal", "journal", "helper", "journal", "helper"]
    assert waits == [(30, False), (60, False), (120, False), (30, True)]


def test_the_monitor_stops_only_on_a_host_without_a_usable_helper(monkeypatch):
    monkeypatch.setattr(_mod, "_reconcile_native_pixel_startup", lambda: (None, None))
    monkeypatch.setattr(_mod, "_pixel_model_hold_pending", lambda: False)
    monkeypatch.setattr(_mod, "_wait_for_pixel_access_check",
                        lambda *_args, **_kwargs: pytest.fail("waited without a helper"))
    assert _mod._monitor_native_pixel_access() is None


class _Helper:
    """The installed, root-owned reconcile helper, as the agent inspects it."""

    def __init__(self, _path):
        self.parents = ()

    def __str__(self):
        return "/usr/local/libexec/ods-pixel-access/pixel_access_reconcile.py"

    def exists(self):
        return True

    def lstat(self):
        return types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0)

    def stat(self):
        return types.SimpleNamespace(st_size=100)

    def read_text(self):
        return "STARTUP_REPROOF_VERSION = 1\n"


@pytest.fixture
def helper(monkeypatch):
    """Answer each protected-helper run with one scripted diagnostic."""
    answers: list[object] = []
    runs: list[list[str]] = []

    def run(argv, **_kwargs):
        runs.append(argv)
        answer = answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if answer is None:
            return types.SimpleNamespace(returncode=0, stderr=b"")
        return types.SimpleNamespace(returncode=1, stderr=json.dumps(answer).encode())

    monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(_mod, "Path", _Helper)
    monkeypatch.setattr(_mod.subprocess, "run", run)
    monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
    return types.SimpleNamespace(answers=answers, runs=runs)


@pytest.mark.parametrize(("diagnostic", "expected"), [
    (None, ("recheck", None)),
    ({"stage": "unsafe-state", "projection": {"scope": "owner-host", "available": True,
                                              "busy": True, "pending": False}}, ("recheck", None)),
    # Read-only refusals: the helper stopped at its status read.
    ({"stage": "unsafe-state", "projection": {"available": True, "busy": False, "pending": True,
                                              "reason": "transition-recovery-required"}}, ATTENTION),
    ({"stage": "unsafe-state", "projection": {"available": True, "busy": False, "pending": False,
                                              "reason": "chat-recovery-required"}},
     ("attention", "stage=unsafe-state reason=chat-recovery-required")),
    ({"stage": "status-unavailable", "projection": {"available": True}},
     ("attention", "stage=status-unavailable reason=None")),
    # A submitted change that failed, or a helper failure that may follow one.
    ({"stage": "change-failed", "projection": {"available": True, "busy": True, "pending": False}},
     ("uncertain", "stage=change-failed reason=None")),
    ({"stage": "client-exception", "exceptionType": "OSError"},
     ("uncertain", "stage=client-exception reason=None")),
    (subprocess.TimeoutExpired("helper", 360), ("uncertain", "stage=helper-failed")),
], ids=["ready", "busy", "pending", "chat-recovery", "status-unavailable",
        "change-failed", "client-exception", "timeout"])
def test_each_helper_answer_maps_to_one_monitor_outcome(helper, diagnostic, expected):
    helper.answers.append(diagnostic)
    assert _mod._reconcile_native_pixel_startup() == expected
    assert helper.runs == [["/usr/bin/python3", "-I",
                            "/usr/local/libexec/ods-pixel-access/pixel_access_reconcile.py", "--startup"]]


@pytest.mark.parametrize("change", [
    {},
    {"configured_mode": "full-access"},
    {"busy": True},
    {"pending": True},
    {"reason": "chat-recovery-required"},
    {"reason": None},
    {"available": False},
    {"scope": "guest"},
    {"effective_mode": "sandboxed", "runtime_verified": True, "reason": None},
], ids=["stale-sandbox", "full-access", "busy", "pending", "chat-recovery", "no-reason",
        "unavailable", "scope", "verified"])
def test_only_an_idle_stale_sandbox_proof_is_eligible_for_a_wake(change):
    assert _mod._pixel_access_stale_sandbox_proof({**STALE_SANDBOX, **change}) is (change == {})
    assert _mod._pixel_access_stale_sandbox_proof(["not", "a", "status"]) is False


class _Handler:
    """Minimal stand-in for BaseHTTPRequestHandler."""

    def __init__(self):
        self.headers = {"Authorization": "Bearer test-key"}
        self.wfile = io.BytesIO()
        self.code = None

    def send_response(self, code):
        self.code = code

    def send_header(self, _name, _value):
        pass

    def end_headers(self):
        pass


@pytest.mark.parametrize(("status", "body", "wakes"), [
    (200, STALE_SANDBOX, True),
    (200, VERIFIED, False),
    (200, {**STALE_SANDBOX, "configured_mode": "full-access"}, False),
    (200, {**STALE_SANDBOX, "pending": True, "reason": "transition-recovery-required"}, False),
    (503, STALE_SANDBOX, False),
], ids=["stale-sandbox", "verified", "full-access", "pending", "unavailable"])
def test_a_status_read_of_a_stale_sandbox_wakes_the_monitor_and_changes_nothing(monkeypatch, status, body, wakes):
    requests: list[tuple] = []

    def relay(operation, request=None, *, config):
        requests.append((operation, request))
        return status, body

    monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
    monkeypatch.setattr(_mod, "load_env", lambda _path: {})
    monkeypatch.setattr(pixel_access_relay, "request_runtime_access", relay)
    handler = _Handler()
    _mod.AgentHandler._handle_pixel_access_mode(handler, False)
    assert (handler.code, json.loads(handler.wfile.getvalue())) == (status, body)
    assert requests == [("status", None)]
    assert _mod._pixel_access_monitor_wake.is_set() is wakes


def test_a_wake_ends_the_wait_after_the_settle_delay(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(_mod.time, "sleep", slept.append)
    done = threading.Event()

    def wait():
        _mod._wait_for_pixel_access_check(60, wake=True)
        done.set()

    waiter = threading.Thread(target=wait)
    waiter.start()
    _mod._pixel_access_monitor_wake.set()
    assert done.wait(5)
    waiter.join(5)
    assert slept == [_mod._PIXEL_ACCESS_WAKE_SETTLE_SECONDS]
    assert not _mod._pixel_access_monitor_wake.is_set()


def test_a_wait_without_wakes_ignores_a_pending_wake(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(_mod.time, "sleep", slept.append)
    _mod._pixel_access_monitor_wake.set()
    _mod._wait_for_pixel_access_check(120, wake=False)
    assert slept == [120]
    # The wake stays for the first check after the latch clears.
    assert _mod._pixel_access_monitor_wake.is_set()


@pytest.mark.parametrize(("journal", "pending"), [
    ({"pending": True, "phase": "held", "transactionId": "a" * 64}, True),
    ({"pending": False, "phase": "completed", "transactionId": "a" * 64}, False),
    ({"pending": False, "phase": "idle", "transactionId": None}, False),
    (RuntimeError("Managed model recovery journal is invalid"), True),
], ids=["held", "completed", "idle", "unreadable"])
def test_an_unreadable_model_journal_counts_as_an_open_transaction(monkeypatch, journal, pending):
    def status():
        if isinstance(journal, BaseException):
            raise journal
        return journal

    monkeypatch.setattr(_mod, "_pixel_model_recovery_status", status)
    assert _mod._pixel_model_hold_pending() is pending


@pytest.mark.parametrize(("answer", "verified"), [
    ((200, VERIFIED), True),
    ((200, {**VERIFIED, "configured_mode": "full-access", "effective_mode": "full-access"}), True),
    ((200, STALE_SANDBOX), False),
    ((200, {**VERIFIED, "pending": True}), False),
    ((200, {**VERIFIED, "reason": "chat-recovery-required"}), False),
    ((409, {"error": "transition-busy"}), False),
    (OSError("socket unavailable"), False),
    (ValueError("invalid access response"), False),
], ids=["sandbox", "full-access", "stale", "pending", "chat-recovery", "refused", "transport", "invalid"])
def test_the_latch_clears_only_on_a_verified_read_only_status(monkeypatch, answer, verified):
    operations = []

    def request(operation, request=None, **_kwargs):
        operations.append((operation, request))
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(pixel_access_client, "request_access", request)
    assert _mod._pixel_access_verified() is verified
    assert operations == [("status", None)]


def test_the_wake_settle_and_backoff_bounds_are_the_documented_ones():
    assert (_mod._PIXEL_ACCESS_RECHECK_SECONDS, _mod._PIXEL_ACCESS_BACKOFF_CAP_SECONDS,
            _mod._PIXEL_ACCESS_WAKE_SETTLE_SECONDS) == (30, 300, 5)
