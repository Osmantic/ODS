import errno
import json
import os
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for p in (REPO, REPO / "bin"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import macos_native_service as mns
from macos_native_service import NativeService, NativeServiceError, main
import pixel_macos_process as pmp


@pytest.fixture
def realnonroot():
    if os.getuid() == 0:
        pytest.skip("requires non-root")
    return os.getuid()


@pytest.fixture
def root(tmp_path, realnonroot):
    r = tmp_path / "install"
    r.mkdir()
    (r / "bin").mkdir()
    (r / "data").mkdir()
    (r / "extensions/services/dashboard-api").mkdir(parents=True)
    exe = r / "bin/llama-server"
    exe.write_bytes(b"#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    return types.SimpleNamespace(root=r, exe=exe, home=home, tmp=tmp_path)


@pytest.fixture
def svc(root):
    return NativeService(root.root, root.exe, root.root / "data/llama.pid", home=root.home)


def _fake_identity(pid, exe, uid=None, gid=None):
    uid = os.getuid() if uid is None else uid
    gid = os.getgid() if gid is None else gid
    return (pid, 1000, 200, uid, gid, uid, gid, uid, gid, str(exe))


def test_status_no_writes_no_pid_authority(svc, root, monkeypatch):
    pidfile = svc.pid_file
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text("99999")
    before = {p: p.read_bytes() for p in root.root.rglob("*") if p.is_file()}
    monkeypatch.setattr(svc, "jobs", lambda: [])
    monkeypatch.setattr(svc, "processes", lambda: [])
    result = svc.status()
    assert result == {"running": False, "identity": None, "managed": False, "domain": None}
    after = {p: p.read_bytes() for p in root.root.rglob("*") if p.is_file()}
    assert before == after


def test_domain_errors_propagate(svc, monkeypatch):
    def boom(*a, **k):
        raise NativeServiceError("Could not inspect native launchd domain")
    monkeypatch.setattr(svc, "run", boom)
    with pytest.raises(NativeServiceError):
        svc.domains()


def test_permission_error_propagates(svc, monkeypatch):
    def boom(*a, **k):
        raise PermissionError(errno.EACCES, "denied")
    monkeypatch.setattr(svc, "run", boom)
    with pytest.raises(PermissionError):
        svc.domains()


def _install_real_model_stores(root):
    src = REPO / "extensions/services/dashboard-api/model_stores.py"
    if not src.exists():
        pytest.skip("real model_stores.py not present")
    dst = root.root / "extensions/services/dashboard-api/model_stores.py"
    dst.write_bytes(src.read_bytes())
    return dst


def test_cross_runtime_managed_job_and_orphan(svc, root, monkeypatch):
    _install_real_model_stores(root)
    other_exe = root.tmp / "other runtime/llama-server"
    other_exe.parent.mkdir()
    other_exe.write_bytes(b"#!/bin/sh\n")
    other_exe.chmod(0o755)
    registry = {"schemaVersion": 1, "stores": [{
        "id": "s1", "profiles": {"m.gguf": {
            "executable": str(other_exe), "backend": "metal", "contextLength": 4096, "mtp": False}},
    }]}
    (root.root / "data/model-stores.json").write_text(json.dumps(registry))
    domain = f"gui/{os.getuid()}"
    job = {"path": str(svc.plist), "program": str(other_exe),
           "working directory": str(root.root), "state": "running", "pid": "4242", "domain": domain}
    state = {"job": job}
    monkeypatch.setattr(NativeService, "domains", lambda self: [domain])
    monkeypatch.setattr(NativeService, "job", lambda self, d: state["job"])
    monkeypatch.setattr(mns.subprocess, "run", lambda *a, **kw:
                        types.SimpleNamespace(returncode=0, stdout=f"4242 {os.getuid()}\n", stderr=""))
    def native(pid, *, uid, gid, executable):
        if executable != str(other_exe):
            raise pmp.ProcessIdentityError("gateway-process-executable-mismatch")
        return _fake_identity(pid, other_exe)
    monkeypatch.setattr(mns, "process_identity", native)
    result = svc.status()
    assert result["running"] is True and result["managed"] is True
    assert result["identity"] == _fake_identity(4242, other_exe)
    # Same kernel-owned process with the launchd job absent is an owned orphan.
    state["job"] = None
    result = svc.status()
    assert result["running"] is True and result["managed"] is False
    assert result["identity"] == _fake_identity(4242, other_exe)


def test_foreign_job_rejected_before_mutations(svc, root, monkeypatch):
    foreign = root.tmp / "foreign/llama-server"
    foreign.parent.mkdir()
    foreign.write_bytes(b"#!/bin/sh\n")
    foreign.chmod(0o755)
    job = {"path": str(svc.plist), "program": str(foreign),
           "working directory": str(root.root), "state": "running",
           "pid": "1", "domain": f"gui/{os.getuid()}"}
    monkeypatch.setattr(svc, "jobs", lambda: [job])
    monkeypatch.setattr(svc, "processes", lambda: [])
    calls = []
    monkeypatch.setattr(svc, "run", lambda *a, **k: calls.append(a) or types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    with pytest.raises(NativeServiceError):
        svc.status()
    assert calls == []


def test_status_pid_birth_changed_rejected(svc, root, monkeypatch):
    job = {"path": str(svc.plist), "program": str(root.exe),
           "working directory": str(root.root), "state": "running",
           "pid": "555", "domain": f"gui/{os.getuid()}"}
    monkeypatch.setattr(svc, "jobs", lambda: [job])
    monkeypatch.setattr(svc, "domain_available", lambda d: True)
    monkeypatch.setattr(svc, "job", lambda d: job)
    monkeypatch.setattr(svc, "capture", lambda d, token=None, timeout=10: _fake_identity(555, root.exe))
    monkeypatch.setattr(svc, "identity", lambda pid: _fake_identity(pid, root.exe, uid=os.getuid()))
    # processes returns a different birth
    monkeypatch.setattr(svc, "processes", lambda: [_fake_identity(555, root.exe, uid=os.getuid())])
    monkeypatch.setattr(svc, "live", lambda ident: False)
    with pytest.raises(NativeServiceError):
        svc.status()


def test_start_single_lock_around_stop_and_ensure(svc, monkeypatch):
    order = []
    real_locked = svc.locked
    import contextlib
    @contextlib.contextmanager
    def tracking_locked():
        order.append("lock-enter")
        with real_locked():
            yield
        order.append("lock-exit")
    monkeypatch.setattr(svc, "locked", tracking_locked)
    monkeypatch.setattr(svc, "_stop", lambda: order.append("stop") or {"stopped": False})
    monkeypatch.setattr(svc, "_ensure", lambda args: order.append("ensure") or {"created": True})
    svc.start(["--x"])
    assert order == ["lock-enter", "stop", "ensure", "lock-exit"]


def test_start_artifact_check_before_stop(svc, root, monkeypatch):
    # Missing executable -> error before lock/stop.
    svc.executable = root.tmp / "missing"
    called = []
    monkeypatch.setattr(svc, "_stop", lambda: called.append("stop"))
    with pytest.raises(NativeServiceError):
        svc.start([])
    assert called == []


def test_orphan_term_kill_fresh_identity(svc, root, monkeypatch):
    identity = _fake_identity(777, root.exe)
    clock, kills = [0.0], []
    monkeypatch.setattr(svc, "live", lambda i: not kills or kills[-1][1] != 9)
    monkeypatch.setattr(os, "kill", lambda pid, sig: kills.append((pid, sig)))
    monkeypatch.setattr(mns.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mns.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    svc.terminate(identity)
    assert kills == [(777, 15), (777, 9)]
    assert clock[0] >= 5


def test_orphan_reused_pid_avoids_signal(svc, root, monkeypatch):
    ident = _fake_identity(888, root.exe)
    monkeypatch.setattr(svc, "live", lambda i: False)
    kills = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: kills.append((pid, sig)))
    svc.terminate(ident)
    assert kills == []


def test_stop_waits_known_process_gone(svc, root, monkeypatch):
    ident = _fake_identity(999, root.exe)
    job = {"path": str(svc.plist), "program": str(root.exe),
           "working directory": str(root.root), "state": "running",
           "pid": "999", "domain": f"gui/{os.getuid()}"}
    monkeypatch.setattr(svc, "jobs", lambda: [job])
    monkeypatch.setattr(svc, "domain_available", lambda d: True)
    monkeypatch.setattr(svc, "job", lambda d: job)
    monkeypatch.setattr(svc, "capture", lambda d, token=None, timeout=10: ident)
    monkeypatch.setattr(svc, "identity", lambda pid: ident)
    monkeypatch.setattr(svc, "processes", lambda: [ident])
    monkeypatch.setattr(svc, "live", lambda i: True)
    monkeypatch.setattr(svc, "run", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(mns.time, "sleep", lambda s: None)
    # remove_job will loop forever since job never disappears; bound it.
    monkeypatch.setattr(mns.time, "monotonic", iter([0, 0, 0, 0, 1000, 1000, 1000, 1000]).__next__)
    with pytest.raises(NativeServiceError):
        svc.stop()


def test_stop_preserves_journal_on_changed_files(svc, root, monkeypatch):
    journal = svc.journal
    journal.parent.mkdir(parents=True, exist_ok=True)
    doc = {"created": True, "token": "a" * 32, "domain": f"gui/{os.getuid()}",
           "install": str(root.root), "executable": str(root.exe), "label": svc.label,
           "identity": None, "plist_sha256": "0" * 64, "prior_plist": None}
    journal.write_text(json.dumps(doc))
    journal.chmod(0o600)
    monkeypatch.setattr(svc, "jobs", lambda: [])
    monkeypatch.setattr(svc, "processes", lambda: [])
    def change_during_shutdown():
        journal.write_text(json.dumps({**doc, "token": "b" * 32}))
        return []
    monkeypatch.setattr(svc, "processes", change_during_shutdown)
    with pytest.raises(NativeServiceError):
        svc.stop()
    assert journal.exists()


def test_cli_dispatch(monkeypatch, root):
    calls = []
    class FakeSvc:
        def __init__(self, *a, **k):
            calls.append(("init", a))
        def start(self, args):
            calls.append(("start", args))
            return {"created": True}
        def stop(self):
            calls.append(("stop",))
            return {"stopped": True}
        def status(self):
            calls.append(("status",))
            return {"running": False}
    monkeypatch.setattr(mns, "NativeService", FakeSvc)
    pid = root.root / "data/x.pid"
    assert main(["start", str(root.root), str(root.exe), str(pid), "--a"]) == 0
    assert main(["stop", str(root.root), str(root.exe), str(pid)]) == 0
    assert main(["status", str(root.root), str(root.exe), str(pid)]) == 0
    assert calls[1] == ("start", ["--a"])
    assert calls[3] == ("stop",)
    assert calls[5] == ("status",)
    assert main(["bogus"]) == 2


def test_native_enumeration_skips_exited_process_but_not_permission_denied(svc, monkeypatch):
    monkeypatch.setattr(mns.subprocess, "run", lambda *a, **kw:
                        types.SimpleNamespace(returncode=0, stdout=f"42 {os.getuid()}\n", stderr=""))
    def exited(pid, **kwargs):
        raise pmp.ProcessIdentityError("gateway-process-mismatch")
    monkeypatch.setattr(mns, "process_identity", exited)
    assert svc.processes() == []
    def denied(pid, **kwargs):
        raise pmp.ProcessIdentityError("gateway-process-unavailable", errno=errno.EPERM)
    monkeypatch.setattr(mns, "process_identity", denied)
    with pytest.raises(NativeServiceError, match="ownership"):
        svc.processes()


def test_private_journal_identifies_previous_runtime_without_registry(svc, root):
    previous = root.tmp / "old runtime/llama-server"
    old = svc.bound(previous)
    old.save({"created": True, "token": "a" * 32, "domain": f"user/{svc.uid}"})
    assert previous in svc.trusted_binaries()
    with pytest.raises(NativeServiceError, match="different deployment"):
        svc.read_journal()
    svc.journal.chmod(0o644)
    with pytest.raises(NativeServiceError, match="custody"):
        svc.trusted_binaries()


def test_stop_refuses_invalid_registry_before_mutation(svc, root, monkeypatch):
    (root.root / "data/model-stores.json").write_text('{"schemaVersion":1,"stores":{}}')
    domain = f"user/{svc.uid}"
    monkeypatch.setattr(svc, "domains", lambda: [domain])
    monkeypatch.setattr(svc, "job", lambda d: {"path": str(svc.plist), "program": str(svc.executable),
                        "working directory": str(svc.root), "domain": domain, "state": "running", "pid": "42"})
    monkeypatch.setattr(svc, "run", lambda *a: pytest.fail("invalid registry must not mutate launchd"))
    with pytest.raises(NativeServiceError, match="registry"):
        svc.stop()
