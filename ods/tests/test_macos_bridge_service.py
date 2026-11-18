import ctypes
import os
import plistlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import macos_bridge_service as bridge_module  # noqa: E402
from macos_bridge_service import BridgeService, _PROBE_CODE  # noqa: E402
from macos_native_service import NativeServiceError, NativeServiceUncertain  # noqa: E402
from pixel_macos_process import ProcessIdentityError  # noqa: E402


class FakeRun:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def ident(pid, exe, uid=None, gid=None):
    uid = os.getuid() if uid is None else uid
    gid = os.getgid() if gid is None else gid
    return (pid, 1, 2, uid, gid, uid, gid, uid, gid, exe)


def make_args(svc, *, host="127.0.0.1", port="8080", target="9090", peer="127.0.0.1"):
    return [str(svc.executable), str(svc.script), "--listen-host", host,
            "--listen-port", port, "--target-port", target,
            "--allow-peer", peer]


@pytest.fixture
def svc(tmp_path, monkeypatch):
    root = tmp_path / "install"
    (root / "bin").mkdir(parents=True)
    script = root / "bin" / "ods-macos-llm-bridge.py"
    script.write_text("# bridge\n")
    script.chmod(0o755)
    exe = Path(sys.executable).resolve()
    home = tmp_path / "home"
    home.mkdir()
    s = BridgeService(root, exe, home=home)
    monkeypatch.setattr(s, "domains", lambda: [f"gui/{s.uid}"])
    monkeypatch.setattr(s, "_probe_kernel_exe", lambda interpreter: str(Path(interpreter).resolve()))
    return s


def job_for(svc, token=None, pid=None, state="running", args=None):
    j = {"path": str(svc.plist), "program": str(svc.executable),
         "working directory": str(svc.root), "state": state,
         "domain": f"gui/{svc.uid}"}
    if token:
        j["token"] = token
    if pid is not None:
        j["pid"] = str(pid)
    if args is not None:
        j["arguments"] = list(args)
    return j


def test_loaded_job_with_same_interpreter_but_wrong_script_is_refused(svc, monkeypatch):
    args = make_args(svc)
    args[1] = str(svc.root / "bin" / "other.py")
    job = job_for(svc, token="a" * 32, pid=42, args=args)
    monkeypatch.setattr(svc, "job", lambda d: job)
    monkeypatch.setattr(svc, "run", lambda *a: pytest.fail("must not mutate"))
    with pytest.raises(NativeServiceError, match="script"):
        svc.ensure(args[1:])


def test_extra_python_flags_are_refused(svc):
    args = make_args(svc)
    args.insert(1, "-O")
    with pytest.raises(NativeServiceError, match="ten"):
        svc.validate_arguments(args)


def test_python_launcher_must_finish_exec_before_identity_is_accepted(svc, monkeypatch):
    expected = ident(42, str(svc.executable))
    calls = []
    def identity(pid):
        calls.append(pid)
        if len(calls) == 1:
            raise ProcessIdentityError("gateway-process-executable-mismatch")
        return expected
    monkeypatch.setattr(svc, "identity", identity)
    monkeypatch.setattr(svc, "job", lambda d: job_for(svc, pid=42, args=make_args(svc)))
    assert svc.capture(f"gui/{svc.uid}") == expected
    assert len(calls) == 3


def test_legacy_interpreter_allowed_but_script_never_trusted(svc):
    trusted = svc.trusted_binaries()
    assert Path("/usr/bin/python3").resolve() in trusted
    assert svc.script not in trusted
    with pytest.raises(NativeServiceError, match="not trusted"):
        svc.bound(svc.script)


def test_correct_ten_tuple_journal_accepted(svc):
    args = make_args(svc)
    receipt = {"created": True, "token": "a" * 32, "domain": f"gui/{svc.uid}",
               "identity": ident(42, str(svc.executable)),
               "arguments": args, "plist_sha256": "0" * 64, "prior_plist": None}
    svc.save(receipt)
    saved = svc.journal_document()
    assert saved["arguments"] == args
    assert tuple(saved["identity"]) == ident(42, str(svc.executable))


def test_journal_wrong_uid_rejected(svc):
    args = make_args(svc)
    bad = list(ident(42, str(svc.executable)))
    bad[3] = svc.uid + 1
    receipt = {"created": True, "token": "a" * 32, "domain": f"gui/{svc.uid}",
               "identity": tuple(bad), "arguments": args,
               "plist_sha256": "0" * 64, "prior_plist": None}
    svc.save(receipt)
    with pytest.raises(NativeServiceError, match="identity"):
        svc.journal_document()


def test_journal_missing_arguments_rejected(svc):
    receipt = {"created": True, "token": "a" * 32, "domain": f"gui/{svc.uid}",
               "identity": ident(42, str(svc.executable)),
               "plist_sha256": "0" * 64, "prior_plist": None}
    svc.save(receipt)
    with pytest.raises(NativeServiceError, match="arguments"):
        svc.journal_document()


def test_refresh_owned_birth_saves_only_when_same_op_and_full_args_match(svc, monkeypatch):
    args = make_args(svc)
    token = "a" * 32
    old = ident(42, str(svc.executable))
    new = list(old)
    new[1] = old[1] + 1
    new = tuple(new)
    receipt = {"created": True, "token": token, "domain": f"gui/{svc.uid}",
               "identity": old, "arguments": args,
               "plist_sha256": "0" * 64, "prior_plist": None}
    svc.save(receipt)
    job = job_for(svc, token=token, pid=42, args=args)
    monkeypatch.setattr(svc, "job", lambda d: job)
    monkeypatch.setattr(svc, "capture", lambda d, token=None, timeout=10: new)
    monkeypatch.setattr(svc, "live", lambda identity: False)
    saved = []
    monkeypatch.setattr(svc, "save", lambda r: saved.append(r))
    svc._refresh_owned_birth()
    assert saved and tuple(saved[0]["identity"]) == new


def test_refresh_skips_save_and_signal_on_token_change(svc, monkeypatch):
    args = make_args(svc)
    receipt = {"created": True, "token": "a" * 32, "domain": f"gui/{svc.uid}",
               "identity": ident(42, str(svc.executable)), "arguments": args,
               "plist_sha256": "0" * 64, "prior_plist": None}
    svc.save(receipt)
    job = job_for(svc, token="b" * 32, pid=42, args=args)
    monkeypatch.setattr(svc, "job", lambda d: job)
    monkeypatch.setattr(svc, "save", lambda r: pytest.fail("must not save"))
    monkeypatch.setattr(svc, "live", lambda identity: pytest.fail("must not signal"))
    with pytest.raises(NativeServiceError, match="token"):
        svc._refresh_owned_birth()


def test_refresh_skips_save_when_original_birth_still_live(svc, monkeypatch):
    args = make_args(svc)
    token = "a" * 32
    old = ident(42, str(svc.executable))
    new = list(old)
    new[1] = old[1] + 1
    new = tuple(new)
    receipt = {"created": True, "token": token, "domain": f"gui/{svc.uid}",
               "identity": old, "arguments": args,
               "plist_sha256": "0" * 64, "prior_plist": None}
    svc.save(receipt)
    job = job_for(svc, token=token, pid=42, args=args)
    monkeypatch.setattr(svc, "job", lambda d: job)
    monkeypatch.setattr(svc, "capture", lambda d, token=None, timeout=10: new)
    monkeypatch.setattr(svc, "live", lambda identity: True)
    monkeypatch.setattr(svc, "save", lambda r: pytest.fail("must not save"))
    with pytest.raises(NativeServiceError, match="overlap"):
        svc._refresh_owned_birth()


def test_refresh_skips_save_when_journal_changed_during_refresh(svc, monkeypatch):
    args = make_args(svc)
    token = "a" * 32
    old = ident(42, str(svc.executable))
    new = list(old)
    new[1] = old[1] + 1
    new = tuple(new)
    receipt = {"created": True, "token": token, "domain": f"gui/{svc.uid}",
               "identity": old, "arguments": args,
               "plist_sha256": "0" * 64, "prior_plist": None}
    svc.save(receipt)
    job = job_for(svc, token=token, pid=42, args=args)
    monkeypatch.setattr(svc, "jobs", lambda: [job])
    monkeypatch.setattr(svc, "owned_job", lambda j: svc)
    monkeypatch.setattr(svc, "bound", lambda e: svc)
    monkeypatch.setattr(svc, "capture", lambda d, token=None, timeout=10: new)
    monkeypatch.setattr(svc, "live", lambda identity: False)
    calls = {"n": 0}
    real = svc.journal_document
    def fake():
        calls["n"] += 1
        doc = real()
        if calls["n"] >= 2:
            return {**doc, "token": "b" * 32}
        return doc
    monkeypatch.setattr(svc, "journal_document", fake)
    monkeypatch.setattr(svc, "save", lambda r: pytest.fail("must not save"))
    with pytest.raises(NativeServiceError, match="changed"):
        svc._refresh_owned_birth()


def test_orphan_only_journal_does_not_enumerate_broad_python(svc, monkeypatch):
    args = make_args(svc)
    receipt = {"created": True, "token": "a" * 32, "domain": f"gui/{svc.uid}",
               "identity": ident(42, str(svc.executable)), "arguments": args,
               "plist_sha256": "0" * 64, "prior_plist": None}
    svc.save(receipt)
    monkeypatch.setattr(svc, "jobs", lambda: [])
    monkeypatch.setattr(svc, "live", lambda identity: True)
    monkeypatch.setattr(bridge_module.subprocess, "run",
                        lambda *a, **k: pytest.fail("must not enumerate processes"))
    found = svc.processes()
    assert found == [ident(42, str(svc.executable))]


def test_launch_payload_single_interpreter_script_keepalive_background(svc, monkeypatch):
    args = make_args(svc)
    monkeypatch.setattr(svc, "domains", lambda: [f"user/{svc.uid}"])
    monkeypatch.setattr(svc, "job", lambda d: None)
    monkeypatch.setattr(svc, "processes", lambda: [])
    monkeypatch.setattr(svc, "prior_plist", lambda: None)
    captured = {}
    monkeypatch.setattr(svc, "atomic", lambda p, d: captured.setdefault("plist", d))
    monkeypatch.setattr(svc, "save", lambda r: None)
    monkeypatch.setattr(svc, "run", lambda *a: FakeRun(0))
    monkeypatch.setattr(svc, "capture", lambda d, token=None, timeout=10: ident(7, str(svc.executable)))
    svc.ensure(args[1:])
    payload = plistlib.loads(captured["plist"])
    assert payload["ProgramArguments"] == args
    assert payload["KeepAlive"] is True
    assert payload["LimitLoadToSessionType"] == "Background"
    assert payload["ProgramArguments"].count(str(svc.executable)) == 1
    assert payload["ProgramArguments"].count(str(svc.script)) == 1


def test_bad_configure_arguments_rejected_before_stop(svc, monkeypatch):
    monkeypatch.setattr(svc, "_stop", lambda: pytest.fail("must not stop"))
    monkeypatch.setattr(svc, "_ensure", lambda a: pytest.fail("must not launch"))
    with pytest.raises(NativeServiceError):
        svc.configure(["--listen-host", "not-an-ip"])


def test_locked_readiness_rollback_keeps_original_token_and_foreign_job(svc, monkeypatch):
    args = make_args(svc)
    token = "a" * 32
    receipt = {"created": True, "token": token, "domain": f"gui/{svc.uid}",
               "identity": ident(42, str(svc.executable)), "arguments": args,
               "plist_sha256": "0" * 64, "prior_plist": None}
    svc.save(receipt)
    monkeypatch.setattr(svc, "_stop", lambda: None)
    monkeypatch.setattr(svc, "_ensure", lambda a: dict(receipt))
    monkeypatch.setattr(svc, "capture", lambda d, token=None, timeout=10: receipt["identity"])
    monkeypatch.setattr(svc, "listener_owned", lambda *a: True)
    def refused(*args, **kwargs):
        raise OSError("refused")
    monkeypatch.setattr(bridge_module.socket, "create_connection", refused)
    foreign = job_for(svc, token="b" * 32, pid=99, args=args)
    monkeypatch.setattr(svc, "job", lambda d: foreign)
    monkeypatch.setattr(svc, "run", lambda *a: pytest.fail("must not bootout foreign job"))
    rollback = svc._rollback
    def check_locked(expected):
        assert expected["token"] == token
        with pytest.raises(NativeServiceError, match="already active"):
            with svc.locked():
                pytest.fail("lifecycle lock released before cleanup")
        rollback(expected)
    monkeypatch.setattr(svc, "_rollback", check_locked)
    with pytest.raises(NativeServiceUncertain):
        svc.configure(args[1:], timeout=0)
    assert svc.journal_document()["token"] == token


@pytest.mark.parametrize("output,owned", [("p42\nf3\nn127.0.0.1:8080\n", True),
                                         ("p42\nf3\nn127.0.0.1:9090\n", False)])
def test_listener_belongs_to_captured_pid_and_configured_address(svc, monkeypatch, output, owned):
    def lsof(argv, **kwargs):
        assert argv == ["/usr/sbin/lsof", "-nP", "-a", "-p", "42", "-i4TCP", "-sTCP:LISTEN", "-Fpn"]
        return FakeRun(0, output)
    monkeypatch.setattr(bridge_module.subprocess, "run", lsof)
    assert svc.listener_owned(ident(42, str(svc.executable)), "127.0.0.1", 8080) is owned


def test_listener_other_pid_is_rejected(svc, monkeypatch):
    monkeypatch.setattr(bridge_module.subprocess, "run", lambda *a, **k: FakeRun(0, "p99\nn127.0.0.1:8080\n"))
    with pytest.raises(NativeServiceError, match="ambiguous"):
        svc.listener_owned(ident(42, str(svc.executable)), "127.0.0.1", 8080)


@pytest.mark.parametrize("own_pid", [7777, 12345])
def test_probe_uses_own_child_pid_not_target_pid(monkeypatch, own_pid):
    seen = {}
    def pidpath(pid, buffer, size):
        seen["pid"] = pid
        path = b"/trusted/framework/Python"
        buffer.value = path
        return len(path)
    fake_ctypes = types.SimpleNamespace(
        CDLL=lambda *a, **k: types.SimpleNamespace(proc_pidpath=pidpath),
        c_int=int, c_uint32=int, c_void_p=int,
        create_string_buffer=ctypes.create_string_buffer,
    )
    fake_os = types.SimpleNamespace(getpid=lambda: own_pid, fsdecode=os.fsdecode)
    def fail(code):
        raise AssertionError(code)
    fake_sys = types.SimpleNamespace(exit=fail, argv=["-c", "99999"],
                                    stdout=types.SimpleNamespace(write=lambda s: seen.update(output=s)))
    with monkeypatch.context() as imports:
        for name, module in {"ctypes": fake_ctypes, "os": fake_os, "sys": fake_sys}.items():
            imports.setitem(sys.modules, name, module)
        exec(compile(_PROBE_CODE, "<probe>", "exec"), {})
    assert seen == {"pid": own_pid, "output": "/trusted/framework/Python"}
