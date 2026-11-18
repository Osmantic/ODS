import errno
import json
import os
import plistlib
import stat
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import macos_native_service as mns  # noqa: E402
import pixel_macos_process as pmp  # noqa: E402
from macos_native_service import NativeService, NativeServiceError  # noqa: E402
from pixel_macos_process import ProcessIdentityError  # noqa: E402


class FakeRun:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def ident(pid, exe, uid=None, gid=None):
    uid = os.getuid() if uid is None else uid
    gid = os.getgid() if gid is None else gid
    return (pid, 1, 2, uid, gid, uid, gid, uid, gid, exe)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    root = tmp_path / "install"
    root.mkdir()
    exe = root / "bin" / "llama-server"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    s = NativeService(root, exe, root / "run.pid", home=home)
    monkeypatch.setattr(s, "domains", lambda: [f"gui/{s.uid}"])
    return s


def job_for(s, token=None, pid=None, state="running"):
    j = {"path": str(s.plist), "program": str(s.executable),
         "working directory": str(s.root), "state": state,
         "domain": f"gui/{s.uid}"}
    if token:
        j["token"] = token
    if pid is not None:
        j["pid"] = str(pid)
    return j


def test_parser_reads_tab_fields_and_marker(svc, monkeypatch):
    text = (
        "\tpath = /x.plist\n"
        "\tprogram = /x/bin\n"
        "\tworking directory = /x\n"
        "\tstate = running\n"
        "\tpid = 42\n"
        "\t\tODS_NATIVE_LAUNCH_TOKEN => " + "a" * 32 + "\n"
    )
    monkeypatch.setattr(svc, "run", lambda *a: FakeRun(0, text, ""))
    j = svc.job(f"gui/{svc.uid}")
    assert j["path"] == "/x.plist"
    assert j["program"] == "/x/bin"
    assert j["working directory"] == "/x"
    assert j["state"] == "running"
    assert j["pid"] == "42"
    assert j["token"] == "a" * 32


def test_parser_rejects_ambiguous_fields(svc, monkeypatch):
    text = "\tpath = /a\n\tpath = /b\n\tprogram = /p\n\tworking directory = /w\n\tstate = running\n"
    monkeypatch.setattr(svc, "run", lambda *a: FakeRun(0, text, ""))
    with pytest.raises(NativeServiceError):
        svc.job(f"gui/{svc.uid}")


def test_ensure_preserves_existing_live_engine(svc, monkeypatch):
    token = "b" * 32
    job = job_for(svc, token=token, pid=1234)
    monkeypatch.setattr(svc, "job", lambda d: job)
    monkeypatch.setattr(svc, "capture", lambda d, token=None, timeout=10: ident(1234, str(svc.executable)))
    calls = []
    monkeypatch.setattr(svc, "run", lambda *a: calls.append(a) or FakeRun(0))
    monkeypatch.setattr(svc, "atomic", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no write")))
    out = svc.ensure(["--port", "1"])
    assert out["created"] is False
    assert out["identity"][0] == 1234
    assert not any(c[0] in {"bootstrap", "bootout"} for c in calls)


def test_ensure_creates_receipt_before_bootstrap(svc, monkeypatch):
    order = []
    monkeypatch.setattr(svc, "job", lambda d: None)
    monkeypatch.setattr(svc, "processes", lambda: [])
    monkeypatch.setattr(svc, "prior_plist", lambda: None)
    monkeypatch.setattr(svc, "save", lambda r: order.append(("save", r.get("identity"))))
    monkeypatch.setattr(svc, "atomic", lambda p, d: order.append(("atomic", str(p))))
    monkeypatch.setattr(svc, "run", lambda *a: order.append(("run", a[0])) or FakeRun(0))
    monkeypatch.setattr(svc, "capture", lambda d, token=None, timeout=10: ident(555, str(svc.executable)))
    out = svc.ensure([])
    assert out["created"] is True
    assert order[0][0] == "save" and order[0][1] is None
    assert order[1][0] == "atomic"
    assert order[2] == ("run", "bootstrap")


def test_ensure_starts_background_when_gui_absent(svc, monkeypatch):
    monkeypatch.setattr(svc, "domains", lambda: [f"user/{svc.uid}"])
    monkeypatch.setattr(svc, "job", lambda d: None)
    monkeypatch.setattr(svc, "processes", lambda: [])
    monkeypatch.setattr(svc, "prior_plist", lambda: None)
    captured = {}
    monkeypatch.setattr(svc, "atomic", lambda p, d: captured.setdefault("plist", d))
    monkeypatch.setattr(svc, "save", lambda r: None)
    monkeypatch.setattr(svc, "run", lambda *a: FakeRun(0))
    monkeypatch.setattr(svc, "capture", lambda d, token=None, timeout=10: ident(7, str(svc.executable)))
    svc.ensure([])
    payload = plistlib.loads(captured["plist"])
    assert payload["LimitLoadToSessionType"] == "Background"


def test_ensure_leaves_replacement_token_job_untouched(svc, monkeypatch):
    job = job_for(svc, token="c" * 32, pid=99)
    monkeypatch.setattr(svc, "job", lambda d: job)
    monkeypatch.setattr(svc, "capture", lambda d, token=None, timeout=10: ident(99, str(svc.executable)))
    monkeypatch.setattr(svc, "run", lambda *a: (_ for _ in ()).throw(AssertionError("no launchctl")))
    out = svc.ensure([])
    assert out["created"] is False


def test_failure_before_identity_capture_cleans_own_job(svc, monkeypatch):
    monkeypatch.setattr(svc, "job", lambda d: None)
    monkeypatch.setattr(svc, "processes", lambda: [])
    monkeypatch.setattr(svc, "prior_plist", lambda: None)
    monkeypatch.setattr(svc, "run", lambda *a: FakeRun(0))
    monkeypatch.setattr(svc, "capture", lambda d, token=None, timeout=10: None)
    removed = []
    monkeypatch.setattr(svc, "remove_job", lambda d, i, token=None: removed.append((d, i, token)))
    with pytest.raises(NativeServiceError):
        svc.ensure([])
    assert removed and removed[0][1] is None


def test_unknown_inspection_error_before_mutation(svc, monkeypatch):
    monkeypatch.setattr(svc, "job", lambda d: (_ for _ in ()).throw(NativeServiceError("boom")))
    monkeypatch.setattr(svc, "run", lambda *a: (_ for _ in ()).throw(AssertionError("no launchctl")))
    with pytest.raises(NativeServiceError):
        svc.ensure([])


def test_job_absence_while_known_process_live_times_out(svc, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    monkeypatch.setattr(svc, "job", lambda domain: None)
    monkeypatch.setattr(svc, "live", lambda identity: True)
    with pytest.raises(NativeServiceError, match="shutdown is not confirmed"):
        svc.remove_job(f"gui/{svc.uid}", ident(42, str(svc.executable)))
    assert clock[0] >= 30


def test_process_appears_during_dead_job_stop_refuses(svc, monkeypatch):
    job = job_for(svc, token="d" * 32, state="exited")
    monkeypatch.setattr(svc, "job", lambda d: job)
    monkeypatch.setattr(svc, "capture", lambda d, token=None, timeout=10: None)
    monkeypatch.setattr(svc, "processes", lambda: [ident(1, str(svc.executable))])
    monkeypatch.setattr(svc, "prior_plist", lambda: None)
    monkeypatch.setattr(svc, "run", lambda *a: (_ for _ in ()).throw(AssertionError("no bootout")))
    with pytest.raises(NativeServiceError):
        svc.ensure([])


def test_malformed_receipt_never_bootout(svc, monkeypatch):
    monkeypatch.setattr(svc, "run", lambda *a: (_ for _ in ()).throw(AssertionError("no launchctl")))
    with pytest.raises(NativeServiceError):
        svc.rollback({"created": True, "token": "nothex", "domain": f"gui/{svc.uid}"})


def test_live_foreign_job_not_adopted(svc, monkeypatch):
    job = job_for(svc, token="e" * 32, pid=1)
    job["program"] = "/other/bin"
    monkeypatch.setattr(svc, "job", lambda d: job)
    monkeypatch.setattr(svc, "run", lambda *a: (_ for _ in ()).throw(AssertionError("no launchctl")))
    with pytest.raises(NativeServiceError):
        svc.ensure([])


def test_held_lock_no_mutations(svc, monkeypatch):
    monkeypatch.setattr(svc, "run", lambda *a: (_ for _ in ()).throw(AssertionError("no launchctl")))
    with svc.locked():
        with pytest.raises(NativeServiceError):
            svc.ensure([])


def test_transient_eperm_capture_retries_but_never_absence(svc, monkeypatch):
    calls = {"n": 0}
    job = job_for(svc, token="f" * 32, pid=42)

    def fake_job(d):
        return job

    def fake_identity(pid):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ProcessIdentityError("gateway-process-unavailable", errno=errno.EPERM)
        return ident(pid, str(svc.executable))

    monkeypatch.setattr(svc, "job", fake_job)
    monkeypatch.setattr(svc, "identity", fake_identity)
    out = svc.capture(f"gui/{svc.uid}", token="f" * 32, timeout=2)
    assert out[0] == 42
    assert calls["n"] >= 3


def test_capture_waits_for_exec_and_rechecks_identity(svc, monkeypatch):
    calls = {"identity": 0, "job": 0}
    good = ident(42, str(svc.executable))

    def identity(pid):
        assert pid == 42
        calls["identity"] += 1
        if calls["identity"] == 1:
            raise ProcessIdentityError("gateway-process-executable-mismatch")
        return good

    def job(domain):
        calls["job"] += 1
        return job_for(svc, token="a" * 32, pid=42)

    monkeypatch.setattr(svc, "identity", identity)
    monkeypatch.setattr(svc, "job", job)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    assert svc.capture(f"gui/{svc.uid}", token="a" * 32) == good
    assert calls == {"identity": 3, "job": 3}


def test_capture_persistent_exec_mismatch_reaches_deadline(svc, monkeypatch):
    clock = [0.0]
    reads = []

    def identity(pid):
        reads.append(pid)
        raise ProcessIdentityError("gateway-process-executable-mismatch")

    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(svc, "identity", identity)
    monkeypatch.setattr(svc, "job", lambda domain: job_for(svc, token="a" * 32, pid=42))
    with pytest.raises(NativeServiceError, match="did not produce a verified process"):
        svc.capture(f"gui/{svc.uid}", token="a" * 32, timeout=.25)
    assert .25 <= clock[0] <= .35
    assert reads == [42] * 4


@pytest.mark.parametrize("reason", ["gateway-process-owner-mismatch", "gateway-process-changed"])
def test_capture_exec_wait_does_not_retry_identity_violations(svc, monkeypatch, reason):
    def identity(pid):
        raise ProcessIdentityError(reason)

    def no_sleep(seconds):
        pytest.fail("identity violations must fail immediately")

    monkeypatch.setattr(svc, "identity", identity)
    monkeypatch.setattr(svc, "job", lambda domain: job_for(svc, token="a" * 32, pid=42))
    monkeypatch.setattr(time, "sleep", no_sleep)
    with pytest.raises(NativeServiceError, match="identity did not match"):
        svc.capture(f"gui/{svc.uid}", token="a" * 32)


def test_capture_exec_wait_rejects_changed_operation(svc, monkeypatch):
    tokens = iter(["a" * 32, "b" * 32])
    reads = []

    def identity(pid):
        reads.append(pid)
        raise ProcessIdentityError("gateway-process-executable-mismatch")

    monkeypatch.setattr(svc, "identity", identity)
    monkeypatch.setattr(svc, "job", lambda domain: job_for(svc, token=next(tokens), pid=42))
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    with pytest.raises(NativeServiceError, match="different deployment or operation"):
        svc.capture(f"gui/{svc.uid}", token="a" * 32)
    assert reads == [42]


def test_live_exec_mismatch_remains_false_without_retry(svc, monkeypatch):
    def identity(pid):
        raise ProcessIdentityError("gateway-process-executable-mismatch")

    def no_sleep(seconds):
        pytest.fail("liveness must not retry a changed executable")

    monkeypatch.setattr(svc, "identity", identity)
    monkeypatch.setattr(time, "sleep", no_sleep)
    assert svc.live(ident(42, str(svc.executable))) is False


def test_rollback_restores_prior_plist(svc, monkeypatch):
    prior = plistlib.dumps({"Label": svc.label, "ProgramArguments": [str(svc.executable)],
                            "WorkingDirectory": str(svc.root)})
    svc.plist.parent.mkdir(parents=True, exist_ok=True)
    svc.plist.write_bytes(prior)
    token = "1" * 32
    receipt = {"created": True, "token": token, "domain": f"gui/{svc.uid}",
               "identity": None, "plist_sha256": __import__("hashlib").sha256(b"new").hexdigest(),
               "prior_plist": __import__("base64").b64encode(prior).decode()}
    svc.save(receipt)
    svc.plist.write_bytes(b"new")
    monkeypatch.setattr(svc, "job", lambda d: None)
    monkeypatch.setattr(svc, "remove_job", lambda d, i, token=None: None)
    svc.rollback(receipt)
    assert svc.plist.read_bytes() == prior
    assert not svc.journal.exists()

def prepare_rollback(svc):
    raw = b"fixture plist bytes"
    svc.atomic(svc.plist, raw)
    receipt = {"created": True, "token": "a" * 32, "domain": f"gui/{svc.uid}",
               "identity": ident(42, str(svc.executable)),
               "plist_sha256": __import__("hashlib").sha256(raw).hexdigest(),
               "prior_plist": None}
    svc.save(receipt)
    return receipt


def test_rollback_does_not_remove_replacement_job(svc, monkeypatch):
    receipt = prepare_rollback(svc)
    replacement = job_for(svc, token="b" * 32, pid=99)
    monkeypatch.setattr(svc, "job", lambda domain: replacement)
    monkeypatch.setattr(svc, "run", lambda *args: pytest.fail("must not bootout replacement"))
    with pytest.raises(NativeServiceError, match="different deployment or operation"):
        svc.rollback(receipt)
    assert svc.journal.exists() and svc.plist.exists()


def test_reused_pid_birth_prevents_bootout(svc, monkeypatch):
    receipt = prepare_rollback(svc)
    monkeypatch.setattr(svc, "job", lambda domain: job_for(svc, token=receipt["token"], pid=42))
    changed = list(receipt["identity"])
    changed[1] += 1
    monkeypatch.setattr(svc, "identity", lambda pid: tuple(changed))
    monkeypatch.setattr(svc, "run", lambda *args: pytest.fail("must not bootout reused PID"))
    with pytest.raises(NativeServiceError, match="replaced"):
        svc.rollback(receipt)
    assert svc.journal.exists()


def test_process_appearing_before_bootout_is_preserved(svc, monkeypatch):
    monkeypatch.setattr(svc, "job", lambda domain: job_for(svc, token="a" * 32, pid=42))
    monkeypatch.setattr(svc, "run", lambda *args: pytest.fail("unwitnessed process must remain"))
    with pytest.raises(NativeServiceError, match="appeared"):
        svc.remove_job(f"gui/{svc.uid}", None, token="a" * 32)


def test_real_domain_classifier_accepts_observed_missing_gui(svc, monkeypatch):
    def run(action, target):
        if target.startswith("gui/"):
            return FakeRun(112, "", f"Bad request.\nCould not find domain for user gui: {svc.uid}\n")
        return FakeRun(0)
    monkeypatch.setattr(svc, "run", run)
    assert NativeService.domains(svc) == [f"user/{svc.uid}"]


def test_domain_classifier_rejects_permission_error(svc, monkeypatch):
    monkeypatch.setattr(svc, "run", lambda *args: FakeRun(1, "", "Operation not permitted"))
    with pytest.raises(NativeServiceError, match="domain"):
        NativeService.domains(svc)


def test_private_atomic_receipt(svc):
    svc.save({"token": "a" * 32})
    info = svc.journal.stat()
    assert stat.S_IMODE(info.st_mode) == 0o600
    assert info.st_uid == svc.uid
    assert json.loads(svc.journal.read_text())["token"] == "a" * 32


def test_profile_start_detects_existing_default_binary(svc, monkeypatch):
    import macos_native_service as module
    default = svc.executable
    svc.executable = svc.root / "registered-profile/llama-server"
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kw:
                        FakeRun(0, f"42 {svc.uid}\n55 -2\n"))
    def native(pid, *, uid, gid, executable):
        if executable == str(default):
            return ident(pid, executable, uid, gid)
        raise ProcessIdentityError("gateway-process-executable-mismatch")
    monkeypatch.setattr(module, "process_identity", native)
    assert svc.processes() == [ident(42, str(default))]


def test_cleanup_failure_is_explicitly_uncertain(svc, monkeypatch):
    from macos_native_service import NativeServiceUncertain
    monkeypatch.setattr(svc, "job", lambda domain: None)
    monkeypatch.setattr(svc, "processes", lambda: [])
    monkeypatch.setattr(svc, "run", lambda *args: FakeRun(5))
    def fail(receipt):
        raise NativeServiceError("cleanup refused")
    monkeypatch.setattr(svc, "_rollback", fail)
    with pytest.raises(NativeServiceUncertain, match="cleanup is incomplete") as error:
        svc.ensure([])
    assert error.value.native_cleanup_uncertain
    assert svc.journal.exists()


def test_failed_bootout_with_absent_job_still_waits_for_process(svc, monkeypatch):
    own = ident(42, str(svc.executable))
    observations = [job_for(svc, token="a" * 32, pid=42), None, None, None]
    monkeypatch.setattr(svc, "job", lambda domain: observations.pop(0) if observations else None)
    # First live check protects bootout; second keeps the post-bootout wait active.
    live = iter([True, True, False])
    monkeypatch.setattr(svc, "live", lambda identity: next(live))
    monkeypatch.setattr(svc, "run", lambda *args: FakeRun(113))
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda delay: sleeps.append(delay))
    svc.remove_job(f"gui/{svc.uid}", own, token="a" * 32)
    assert sleeps == [.1]


def test_unresolved_journal_is_not_overwritten_by_new_launch(svc, monkeypatch):
    receipt = prepare_rollback(svc)
    original = svc.journal.read_bytes()
    monkeypatch.setattr(svc, "job", lambda domain: None)
    monkeypatch.setattr(svc, "processes", lambda: [])
    def refuse(saved):
        assert saved["token"] == receipt["token"]
        raise NativeServiceError("recovery remains uncertain")
    monkeypatch.setattr(svc, "_rollback", refuse)
    monkeypatch.setattr(svc, "run", lambda *args: pytest.fail("must not start before recovery"))
    with pytest.raises(NativeServiceError, match="recovery remains"):
        svc.ensure([])
    assert svc.journal.read_bytes() == original


def test_journal_from_other_runtime_is_not_adopted(svc):
    prepare_rollback(svc)
    svc.executable = svc.root / "different-runtime"
    with pytest.raises(NativeServiceError, match="different deployment"):
        svc.read_journal()


class _FakeLib:
    def __init__(self, path_result):
        self.path_result = path_result
        self.path_calls = 0

    def proc_pidpath(self, pid, buffer, size):
        self.path_calls += 1
        length, err = self.path_result
        if length > 0:
            data = b"/usr/bin/true"
            buffer.raw = data + b"\0" * (size - len(data))
        if err is not None:
            pmp.ctypes.set_errno(err)
        return length


def _make_service(monkeypatch, *, identity, birth):
    service = object.__new__(mns.NativeService)
    def fake_identity(pid):
        if isinstance(identity, Exception):
            raise identity
        return identity

    def fake_birth(pid):
        if isinstance(birth, Exception):
            raise birth
        return birth

    monkeypatch.setattr(service, "identity", fake_identity)
    monkeypatch.setattr(mns, "process_birth", fake_birth, raising=False)
    return service


# --- pixel_macos_process helper tests -------------------------------------


def _patch_library(monkeypatch, fake_lib):
    monkeypatch.setattr(pmp, "_library", lambda: fake_lib)
    monkeypatch.setattr(pmp, "_record", lambda lib, pid: (
        pid, 100, 0, 501, 20, 501, 20, 501, 20))


def test_helper_path_esrch_propagated(monkeypatch):
    fake = _FakeLib((0, errno.ESRCH))
    _patch_library(monkeypatch, fake)
    with pytest.raises(pmp.ProcessIdentityError) as excinfo:
        pmp.process_identity(1234, uid=501, gid=20, executable="/usr/bin/true")
    assert str(excinfo.value) == "gateway-process-path-unavailable"
    assert excinfo.value.errno == errno.ESRCH


def test_helper_path_eperm_propagated(monkeypatch):
    fake = _FakeLib((0, errno.EPERM))
    _patch_library(monkeypatch, fake)
    with pytest.raises(pmp.ProcessIdentityError) as excinfo:
        pmp.process_identity(1234, uid=501, gid=20, executable="/usr/bin/true")
    assert str(excinfo.value) == "gateway-process-path-unavailable"
    assert excinfo.value.errno == errno.EPERM


def test_helper_stale_errno_reset(monkeypatch):
    fake = _FakeLib((0, None))
    _patch_library(monkeypatch, fake)
    pmp.ctypes.set_errno(errno.ESRCH)
    with pytest.raises(pmp.ProcessIdentityError) as excinfo:
        pmp.process_identity(1234, uid=501, gid=20, executable="/usr/bin/true")
    assert excinfo.value.errno == 0


@pytest.mark.parametrize("length", [4, 4096])
def test_helper_malformed_positive_length_ambiguous(monkeypatch, length):
    fake = _FakeLib((length, errno.ESRCH))
    _patch_library(monkeypatch, fake)
    with pytest.raises(pmp.ProcessIdentityError) as excinfo:
        pmp.process_identity(1234, uid=501, gid=20, executable="/usr/bin/true")
    assert str(excinfo.value) == "gateway-process-path-unavailable"
    assert excinfo.value.errno is None


# --- manager live() tests -------------------------------------------------


def _identity(pid=1234, sec=100, usec=0):
    return (pid, sec, usec, 501, 20, 501, 20, 501, 20, "/usr/bin/true")


def test_manager_live_false_on_path_esrch(monkeypatch):
    err = pmp.ProcessIdentityError("gateway-process-path-unavailable", errno=errno.ESRCH)
    service = _make_service(monkeypatch, identity=err, birth=(100, 0))
    assert service.live(_identity()) is False


def test_manager_live_false_on_path_0_birth_esrch(monkeypatch):
    err = pmp.ProcessIdentityError("gateway-process-path-unavailable", errno=0)
    birth_err = pmp.ProcessIdentityError("gateway-process-unavailable", errno=errno.ESRCH)
    service = _make_service(monkeypatch, identity=err, birth=birth_err)
    assert service.live(_identity()) is False


def test_manager_live_false_on_changed_birth(monkeypatch):
    err = pmp.ProcessIdentityError("gateway-process-path-unavailable", errno=0)
    service = _make_service(monkeypatch, identity=err, birth=(200, 0))
    assert service.live(_identity()) is False


def test_manager_raises_on_unchanged_birth_path_eperm(monkeypatch):
    err = pmp.ProcessIdentityError("gateway-process-path-unavailable", errno=errno.EPERM)
    service = _make_service(monkeypatch, identity=err, birth=(100, 0))
    with pytest.raises(mns.NativeServiceError):
        service.live(_identity())


def test_manager_raises_on_unchanged_birth_path_0(monkeypatch):
    err = pmp.ProcessIdentityError("gateway-process-path-unavailable", errno=0)
    service = _make_service(monkeypatch, identity=err, birth=(100, 0))
    with pytest.raises(mns.NativeServiceError):
        service.live(_identity())


def test_manager_raises_on_birth_eperm(monkeypatch):
    err = pmp.ProcessIdentityError("gateway-process-path-unavailable", errno=0)
    birth_err = pmp.ProcessIdentityError("gateway-process-unavailable", errno=errno.EPERM)
    service = _make_service(monkeypatch, identity=err, birth=birth_err)
    with pytest.raises(mns.NativeServiceError):
        service.live(_identity())


def test_manager_unrelated_error_does_not_call_birth(monkeypatch):
    err = pmp.ProcessIdentityError("gateway-process-specification-invalid")
    calls = []

    def fake_birth(pid):
        calls.append(pid)
        return (100, 0)

    service = _make_service(monkeypatch, identity=err, birth=(100, 0))
    monkeypatch.setattr(mns, "process_birth", fake_birth, raising=False)
    with pytest.raises(mns.NativeServiceError):
        service.live(_identity())
    assert calls == []


# --- terminate() tests ----------------------------------------------------


def test_terminate_first_identity_matches_then_path_failure_fresh_birth_esrch(monkeypatch):
    identity = _identity()
    path_err = pmp.ProcessIdentityError("gateway-process-path-unavailable", errno=0)
    birth_err = pmp.ProcessIdentityError("gateway-process-unavailable", errno=errno.ESRCH)

    state = {"calls": 0}

    def fake_identity(pid):
        state["calls"] += 1
        if state["calls"] == 1:
            return identity
        raise path_err

    def fake_birth(pid):
        raise birth_err

    service = object.__new__(mns.NativeService)
    monkeypatch.setattr(service, "identity", fake_identity)
    monkeypatch.setattr(mns, "process_birth", fake_birth, raising=False)

    signals = []
    monkeypatch.setattr(mns.os, "kill", lambda pid, sig: signals.append((pid, sig)))

    service.terminate(identity)
    assert signals == [(identity[0], 15)]


def test_terminate_reused_birth_same_pid_no_signal(monkeypatch):
    identity = _identity()
    path_err = pmp.ProcessIdentityError("gateway-process-path-unavailable", errno=0)

    def fake_identity(pid):
        raise path_err

    def fake_birth(pid):
        return (999, 0)

    service = object.__new__(mns.NativeService)
    monkeypatch.setattr(service, "identity", fake_identity)
    monkeypatch.setattr(mns, "process_birth", fake_birth, raising=False)

    signals = []
    monkeypatch.setattr(mns.os, "kill", lambda pid, sig: signals.append((pid, sig)))

    service.terminate(identity)
    assert signals == []


def test_terminate_ambiguous_same_birth_no_signal(monkeypatch):
    identity = _identity()
    path_err = pmp.ProcessIdentityError("gateway-process-path-unavailable", errno=errno.EPERM)

    def fake_identity(pid):
        raise path_err

    def fake_birth(pid):
        return (identity[1], identity[2])

    service = object.__new__(mns.NativeService)
    monkeypatch.setattr(service, "identity", fake_identity)
    monkeypatch.setattr(mns, "process_birth", fake_birth, raising=False)

    signals = []
    monkeypatch.setattr(mns.os, "kill", lambda pid, sig: signals.append((pid, sig)))

    with pytest.raises(mns.NativeServiceError):
        service.terminate(identity)
    assert signals == []
