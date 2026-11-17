import errno
import os
import subprocess
from pathlib import Path

import pytest

from test_host_agent import _mod


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


from pixel_macos_process import ProcessIdentityError


def _FakeIdentityError(errno_value=None, message="gateway-process-executable-mismatch"):
    return ProcessIdentityError(message, errno=errno_value)


def identity(pid=123, uid=501, gid=20, executable="/x", birth=42):
    return (pid, birth, 0, uid, gid, uid, gid, uid, gid, executable)


@pytest.fixture
def install_dir(tmp_path, monkeypatch):
    d = tmp_path / "install"
    (d / "bin").mkdir(parents=True)
    (d / "data").mkdir(parents=True)
    (d / ".env").write_text("GGUF_FILE=model.gguf\n")
    monkeypatch.setattr(_mod, "INSTALL_DIR", d)
    return d


@pytest.fixture
def fake_identity(monkeypatch):
    """Install a controllable process_identity into pixel_macos_process."""
    import pixel_macos_process

    state = {"calls": [], "handler": None}

    def fake(pid, *, uid, gid, executable):
        state["calls"].append((pid, uid, gid, executable))
        return state["handler"](pid, uid, gid, executable)

    monkeypatch.setattr(pixel_macos_process, "process_identity", fake)
    return state


@pytest.fixture
def fake_ps(monkeypatch):
    state = {"stdout": "", "returncode": 0, "calls": []}

    def fake_run(cmd, **kwargs):
        state["calls"].append((cmd, kwargs))
        if cmd[:2] == ["ps", "-axo"]:
            return _FakeCompleted(state["returncode"], state["stdout"], "")
        raise AssertionError(f"unexpected subprocess: {cmd}")

    monkeypatch.setattr(_mod.subprocess, "run", fake_run)
    return state


@pytest.fixture
def fake_launchctl(monkeypatch):
    """Map (domain, service) -> (returncode, stderr)."""
    state = {"responses": {}, "calls": []}

    def fake_run(cmd, **kwargs):
        state["calls"].append((cmd, kwargs))
        if cmd[0] != "launchctl":
            raise AssertionError(f"unexpected subprocess: {cmd}")
        key = tuple(cmd[2:])
        rc, err = state["responses"].get(key, (113, ""))
        return _FakeCompleted(rc, "", err)

    monkeypatch.setattr(_mod.subprocess, "run", fake_run)
    return state


@pytest.fixture
def fake_profile(monkeypatch):
    state = {"profile": None, "calls": []}

    def fake(install_data, gguf):
        state["calls"].append((install_data, gguf))
        return state["profile"]

    monkeypatch.setattr(_mod._model_stores, "registered_runtime_profile", fake)
    return state


# ---------------------------------------------------------------------------
# _macos_native_llama_binaries
# ---------------------------------------------------------------------------

def test_binaries_default_only(install_dir, fake_profile):
    fake_profile["profile"] = None
    result = _mod._macos_native_llama_binaries({"GGUF_FILE": "m.gguf"})
    assert result == ((install_dir / "bin" / "llama-server").resolve(),)
    assert fake_profile["calls"] == [(install_dir / "data", "m.gguf")]


def test_binaries_includes_registered_profile(install_dir, fake_profile, tmp_path):
    exe = tmp_path / "custom" / "llama-server"
    exe.parent.mkdir()
    exe.write_text("")
    fake_profile["profile"] = {"executable": str(exe)}
    result = _mod._macos_native_llama_binaries({"GGUF_FILE": "m.gguf"})
    assert result == (
        (install_dir / "bin" / "llama-server").resolve(),
        exe.resolve(),
    )


def test_binaries_rejects_relative_profile(install_dir, fake_profile):
    fake_profile["profile"] = {"executable": "relative/llama-server"}
    with pytest.raises(RuntimeError, match="absolute"):
        _mod._macos_native_llama_binaries({"GGUF_FILE": "m.gguf"})


# ---------------------------------------------------------------------------
# _macos_native_llama_processes
# ---------------------------------------------------------------------------

def test_processes_ps_enumeration_no_argv(install_dir, fake_profile, fake_ps, fake_identity):
    fake_profile["profile"] = None
    uid = os.getuid()
    fake_ps["stdout"] = f"  100 {uid}\n  200 {uid + 1}\n"
    fake_identity["handler"] = lambda pid, u, g, e: identity(pid, u, g, e)
    result = _mod._macos_native_llama_processes({"GGUF_FILE": "m.gguf"})
    assert result == (identity(100, uid, os.getgid(), str((install_dir / "bin" / "llama-server").resolve())),)
    # ps must not request argv
    ps_cmd = fake_ps["calls"][0][0]
    assert ps_cmd == ["ps", "-axo", "pid=,uid="]


def test_processes_owned_spaced_quoted_pathname(install_dir, fake_profile, fake_ps, fake_identity, tmp_path):
    weird = tmp_path / "dir with space" / "llama's \"server\""
    weird.parent.mkdir()
    weird.write_text("")
    fake_profile["profile"] = {"executable": str(weird)}
    uid = os.getuid()
    fake_ps["stdout"] = f"  300 {uid}\n"
    seen = []

    def handler(pid, u, g, e):
        seen.append(e)
        if e == str(weird.resolve()):
            return identity(pid, u, g, e, 7)
        raise _FakeIdentityError(message="gateway-process-executable-mismatch")

    fake_identity["handler"] = handler
    result = _mod._macos_native_llama_processes({"GGUF_FILE": "m.gguf"})
    assert result == (identity(300, uid, os.getgid(), str(weird.resolve()), 7),)
    assert str(weird.resolve()) in seen


def test_processes_foreign_same_basename_ignored(install_dir, fake_profile, fake_ps, fake_identity):
    fake_profile["profile"] = None
    uid = os.getuid()
    fake_ps["stdout"] = f"  400 {uid}\n"
    fake_identity["handler"] = lambda pid, u, g, e: (_ for _ in ()).throw(
        _FakeIdentityError(message="gateway-process-executable-mismatch")
    )
    result = _mod._macos_native_llama_processes({"GGUF_FILE": "m.gguf"})
    assert result == ()


def test_processes_esrch_skipped(install_dir, fake_profile, fake_ps, fake_identity):
    fake_profile["profile"] = None
    uid = os.getuid()
    fake_ps["stdout"] = f"  500 {uid}\n"
    fake_identity["handler"] = lambda pid, u, g, e: (_ for _ in ()).throw(
        _FakeIdentityError(errno_value=errno.ESRCH)
    )
    assert _mod._macos_native_llama_processes({"GGUF_FILE": "m.gguf"}) == ()


def test_processes_unknown_error_fails_closed(install_dir, fake_profile, fake_ps, fake_identity):
    fake_profile["profile"] = None
    uid = os.getuid()
    fake_ps["stdout"] = f"  600 {uid}\n"
    fake_identity["handler"] = lambda pid, u, g, e: (_ for _ in ()).throw(
        _FakeIdentityError(errno_value=errno.EACCES, message="boom")
    )
    with pytest.raises(RuntimeError, match="ownership"):
        _mod._macos_native_llama_processes({"GGUF_FILE": "m.gguf"})


# ---------------------------------------------------------------------------
# _macos_native_llama_present
# ---------------------------------------------------------------------------

def test_present_gui_domain_confirmed(install_dir, fake_launchctl, monkeypatch):
    uid = os.getuid()
    fake_launchctl["responses"][(f"gui/{uid}/com.ods.llama-server",)] = (0, "")
    monkeypatch.setattr(_mod, "_macos_native_llama_processes", lambda env: ())
    assert _mod._macos_native_llama_present() is True


def test_present_user_domain_job_recognized(install_dir, fake_launchctl, monkeypatch):
    uid = os.getuid()
    fake_launchctl["responses"][(f"gui/{uid}/com.ods.llama-server",)] = (113, "")
    fake_launchctl["responses"][(f"user/{uid}/com.ods.llama-server",)] = (0, "")
    monkeypatch.setattr(_mod, "_macos_native_llama_processes", lambda env: ())
    assert _mod._macos_native_llama_present() is True


def test_present_permission5_fails_closed(install_dir, fake_launchctl, monkeypatch):
    uid = os.getuid()
    fake_launchctl["responses"][(f"gui/{uid}/com.ods.llama-server",)] = (5, "")
    fake_launchctl["responses"][(f"gui/{uid}",)] = (5, "Operation not permitted\n")
    with pytest.raises(RuntimeError, match="LaunchAgent"):
        _mod._macos_native_llama_present()


def test_present_domain_absent_then_processes(install_dir, fake_launchctl, monkeypatch):
    uid = os.getuid()
    fake_launchctl["responses"][(f"gui/{uid}/com.ods.llama-server",)] = (5, "")
    fake_launchctl["responses"][(f"gui/{uid}",)] = (1, f"Could not find domain for gui/{uid}\n")
    fake_launchctl["responses"][(f"user/{uid}/com.ods.llama-server",)] = (5, "")
    fake_launchctl["responses"][(f"user/{uid}",)] = (1, f"Could not find domain for user/{uid}\n")
    monkeypatch.setattr(_mod, "_macos_native_llama_processes", lambda env: ((1, 2, 3, "x", 4),))
    assert _mod._macos_native_llama_present() is True


# ---------------------------------------------------------------------------
# _macos_native_llama_identity_live
# ---------------------------------------------------------------------------

def test_identity_live_stale_pid_ignored(install_dir, fake_identity):
    fake_identity["handler"] = lambda pid, u, g, e: (_ for _ in ()).throw(
        _FakeIdentityError(errno_value=errno.ESRCH)
    )
    assert _mod._macos_native_llama_identity_live(identity(birth=9)) is False


def test_identity_live_pid_reuse_owner_path_birth(install_dir, fake_identity):
    fake_identity["handler"] = lambda pid, u, g, e: identity(pid,u,g,e,999)
    assert _mod._macos_native_llama_identity_live(identity(birth=9)) is False


# ---------------------------------------------------------------------------
# _wait_for_macos_native_llama_health
# ---------------------------------------------------------------------------

def test_wait_cold_new_exit_immediately(install_dir, monkeypatch):
    monkeypatch.setattr(_mod, "_macos_native_llama_identity_live", lambda ident: False)
    monkeypatch.setattr(_mod, "_runtime_health", lambda env: "ok")
    with pytest.raises(RuntimeError, match="exited before becoming healthy"):
        _mod._wait_for_macos_native_llama_health({}, expected_identity=identity())


def test_wait_cold_new_exit_after_2sec(install_dir, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(_mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(_mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    calls = {"n": 0}

    def live(ident):
        calls["n"] += 1
        return calls["n"] < 2  # alive first, dead second

    monkeypatch.setattr(_mod, "_macos_native_llama_identity_live", live)
    monkeypatch.setattr(_mod, "_runtime_health", lambda env: "starting")
    with pytest.raises(RuntimeError, match="exited before becoming healthy"):
        _mod._wait_for_macos_native_llama_health({}, expected_identity=identity())
    assert clock["t"] == 2


def test_wait_healthy_after_4sec(install_dir, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(_mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(_mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(_mod, "_macos_native_llama_identity_live", lambda ident: True)
    monkeypatch.setattr(_mod, "_runtime_health", lambda env: "ok" if clock["t"] >= 4 else "starting")
    _mod._wait_for_macos_native_llama_health({}, expected_identity=identity())
    assert clock["t"] == 4


def test_wait_300_bound(install_dir, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(_mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(_mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(_mod, "_macos_native_llama_identity_live", lambda ident: True)
    monkeypatch.setattr(_mod, "_runtime_health", lambda env: "starting")
    with pytest.raises(RuntimeError, match="did not become healthy"):
        _mod._wait_for_macos_native_llama_health({}, expected_identity=identity())
    assert clock["t"] == 300


def test_wait_health_ok_then_identity_changed(install_dir, monkeypatch):
    monkeypatch.setattr(_mod.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(_mod.time, "sleep", lambda s: None)
    seq = iter([True, False])
    monkeypatch.setattr(_mod, "_macos_native_llama_identity_live", lambda ident: next(seq))
    monkeypatch.setattr(_mod, "_runtime_health", lambda env: "ok")
    with pytest.raises(RuntimeError, match="changed during health verification"):
        _mod._wait_for_macos_native_llama_health({}, expected_identity=identity())

def test_negative_system_uid_is_not_an_inspection_error(install_dir, fake_profile, fake_ps, fake_identity):
    fake_ps["stdout"] = "71074 -2\n1 0\n"
    assert _mod._macos_native_llama_processes({}) == ()
    assert fake_identity["calls"] == []


@pytest.mark.parametrize("row", ["12 owner", "12 501 extra", "-1 501", "12 99999999999"])
def test_malformed_enumeration_fails_closed(install_dir, fake_profile, fake_ps, row):
    fake_ps["stdout"] = row
    with pytest.raises(RuntimeError, match="parse"):
        _mod._macos_native_llama_processes({})


@pytest.mark.parametrize("error", ["gateway-process-owner-mismatch", "gateway-process-changed", "gateway-process-path-unavailable"])
def test_uncertain_enumerated_process_is_not_absence(install_dir, fake_profile, fake_ps, fake_identity, error):
    fake_ps["stdout"] = f"123 {os.getuid()}"
    def fail(*args):
        raise ProcessIdentityError(error)
    fake_identity["handler"] = fail
    with pytest.raises(RuntimeError, match="ownership"):
        _mod._macos_native_llama_processes({})


@pytest.mark.parametrize("error", ["gateway-process-owner-mismatch", "gateway-process-executable-mismatch", "gateway-process-changed"])
def test_reused_pid_never_proves_original_process_live(install_dir, fake_identity, error):
    def fail(*args):
        raise ProcessIdentityError(error)
    fake_identity["handler"] = fail
    assert not _mod._macos_native_llama_identity_live(identity())
