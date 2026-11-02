import ast
import os
import signal
import subprocess
import sys
import threading
import shlex
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux flock protocol")

REPO_ROOT = Path(__file__).resolve().parents[1]
HOST_AGENT = REPO_ROOT / "bin" / "ods-host-agent.py"
SHELL_LIB = REPO_ROOT / "installers" / "lib" / "model-lifecycle-lock.sh"


def _extract_defs():
    tree = ast.parse(HOST_AGENT.read_text())
    wanted = {"_ModelLifecycleMutex", "_run_update_script"}
    nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in wanted]
    assert {n.name for n in nodes} == wanted
    return nodes


@pytest.fixture
def host_ns(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO_ROOT / "bin"))
    try:
        import model_lifecycle_lock  # noqa: F401
    finally:
        sys.path.pop(0)

    lock_root = tmp_path / "locks"
    lock_root.mkdir(mode=0o700)
    install_dir = tmp_path / "install"
    install_dir.mkdir()

    monkeypatch.setenv("ODS_MODEL_LIFECYCLE_LOCK_ROOT", str(lock_root))
    monkeypatch.delenv("INSTALL_USER", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)

    import logging
    ns = {
        "os": os,
        "sys": sys,
        "signal": signal,
        "subprocess": subprocess,
        "threading": threading,
        "logging": logging,
        "logger": logging.getLogger("test-host-agent"),
        "Path": Path,
        "INSTALL_DIR": install_dir,
        "_process_model_lock": model_lifecycle_lock,
    }
    for node in _extract_defs():
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(HOST_AGENT), "exec"), ns)
    return ns


def test_mutex_rejects_external_lease_and_releases(host_ns):
    Mutex = host_ns["_ModelLifecycleMutex"]
    lock_mod = host_ns["_process_model_lock"]
    install_dir = host_ns["INSTALL_DIR"]

    external = lock_mod.acquire(install_dir, blocking=False)
    try:
        m = Mutex()
        assert m.acquire(blocking=False) is False
        assert m.locked() is False
        assert m.lease_fd is None
    finally:
        external.close()

    m = Mutex()
    assert m.acquire(blocking=False) is True
    assert m.locked() is True
    assert isinstance(m.lease_fd, int)
    fd = m.lease_fd
    m.release()
    assert m.locked() is False
    assert m.lease_fd is None
    try:
        os.fstat(fd)
    except OSError:
        pass
    else:
        raise AssertionError("lease fd still open after release")

    # After release, a fresh external lease must succeed.
    again = lock_mod.acquire(install_dir, blocking=False)
    again.close()


def test_update_child_inherits_fd_and_parent_excludes_competitor(host_ns, tmp_path):
    Mutex = host_ns["_ModelLifecycleMutex"]
    lock_mod = host_ns["_process_model_lock"]
    run_update = host_ns["_run_update_script"]
    install_dir = host_ns["INSTALL_DIR"]

    assert SHELL_LIB.exists(), SHELL_LIB
    started = tmp_path / "started"
    released = tmp_path / "released"
    script = tmp_path / "ods-update.sh"
    script.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f"source {shlex.quote(str(SHELL_LIB))}\n"
        f"ods_model_lifecycle_lock_acquire {shlex.quote(str(install_dir))} test-child\n"
        f"touch {shlex.quote(str(started))}\n"
        "ods_model_lifecycle_lock_release\n"
        f"touch {shlex.quote(str(released))}\n"
    )
    script.chmod(0o755)

    host_ns["_find_update_script"] = lambda: script
    host_ns["_update_command"] = lambda s, *args: [str(s), *args]

    m = Mutex()
    assert m.acquire(blocking=False) is True
    fd = m.lease_fd
    assert isinstance(fd, int)

    proc = None
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); "
             "import model_lifecycle_lock as m; "
             "m.acquire(sys.argv[2], blocking=False)",
             str(REPO_ROOT / "bin"), str(install_dir)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        out, err = proc.communicate(timeout=10)
        assert proc.returncode == 1 and "BlockingIOError" in err, (out, err)

        result = run_update("apply", timeout=30, lifecycle_fd=fd)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert started.exists()
        assert released.exists()

        # Parent still holds the lease after child released its shell lease.
        proc2 = subprocess.Popen(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); "
             "import model_lifecycle_lock as m; "
             "m.acquire(sys.argv[2], blocking=False)",
             str(REPO_ROOT / "bin"), str(install_dir)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            out2, err2 = proc2.communicate(timeout=10)
        finally:
            if proc2.poll() is None:
                proc2.kill()
                proc2.wait(timeout=5)
        assert proc2.returncode == 1 and "BlockingIOError" in err2, (out2, err2)
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        m.release()

    # After parent release, competitor can acquire.
    final = lock_mod.acquire(install_dir, blocking=False)
    final.close()


def test_update_timeout_kills_descendants_and_releases_lease(host_ns, tmp_path):
    pid_file = tmp_path / "descendant.pid"
    script = tmp_path / "timed-update.sh"
    # The direct shell exits on TERM. Its child ignores TERM and retains both
    # pipes and the lease: cleanup must address the entire session's group.
    script.write_text(
        "#!/bin/bash\n"
        f"{shlex.quote(sys.executable)} -c "
        + shlex.quote(
            "import os,signal,time; from pathlib import Path; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(30)"
        ) + " &\nwait\n"
    )
    script.chmod(0o700)
    host_ns["_find_update_script"] = lambda: script
    host_ns["_update_command"] = lambda path, *args: [str(path), *args]
    mutex = host_ns["_ModelLifecycleMutex"]()
    assert mutex.acquire(False)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            host_ns["_run_update_script"]("update", timeout=1, lifecycle_fd=mutex.lease_fd)
        assert pid_file.exists(), "fixture descendant never started"
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = Path(f"/proc/{pid}/stat")
            try:
                state = status.read_text().split(") ", 1)[1]
            except (FileNotFoundError, ProcessLookupError):
                break
            if state.startswith("Z "):
                break
            time.sleep(.02)
        else:
            pytest.fail("update descendant survived timeout cleanup")
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
        mutex.release()
    host_ns["_process_model_lock"].acquire(host_ns["INSTALL_DIR"]).close()


def test_non_linux_update_retains_portable_runner(host_ns):
    host_ns["sys"] = SimpleNamespace(platform="darwin")
    calls = []
    host_ns["_find_update_script"] = lambda: Path("unused")
    host_ns["_update_command"] = lambda path, *args: [str(path), *args]
    host_ns["subprocess"] = SimpleNamespace(run=lambda *args, **kwargs: calls.append(kwargs))
    host_ns["_run_update_script"]("check", timeout=5)
    assert calls == [{"cwd": str(host_ns["INSTALL_DIR"]), "capture_output": True,
                      "text": True, "timeout": 5}]


def test_unleased_linux_child_cannot_borrow_another_operation(host_ns, monkeypatch, tmp_path):
    script = tmp_path / "check.sh"
    script.write_text('#!/bin/bash\n[[ -z "${ODS_MODEL_LIFECYCLE_LOCK_FD:-}" && -z "${ODS_MODEL_LIFECYCLE_LOCK_FILE:-}" ]]\n')
    script.chmod(0o700)
    host_ns["_find_update_script"] = lambda: script
    host_ns["_update_command"] = lambda path, *args: [str(path), *args]
    mutex = host_ns["_ModelLifecycleMutex"]()
    assert mutex.acquire(False)
    try:
        monkeypatch.setenv("ODS_MODEL_LIFECYCLE_LOCK_FD", str(mutex.lease_fd))
        monkeypatch.setenv("ODS_MODEL_LIFECYCLE_LOCK_FILE", "stale inherited hint")
        host_ns["_model_lifecycle_lock"] = mutex
        result = host_ns["_run_update_script"]("check", timeout=5)
        assert result.returncode == 0, result.stderr
        with pytest.raises(BlockingIOError):
            host_ns["_process_model_lock"].acquire(host_ns["INSTALL_DIR"])
    finally:
        mutex.release()


def test_shell_rejects_wrong_inherited_descriptor(host_ns, tmp_path):
    wrong_file = tmp_path / "unrelated"
    fd = os.open(wrong_file, os.O_CREAT | os.O_RDWR, 0o600)
    locks, install = host_ns["_process_model_lock"], host_ns["INSTALL_DIR"]
    locks.prepare_lock_path(install)
    code = f'. {shlex.quote(str(SHELL_LIB))}; ods_model_lifecycle_lock_acquire {shlex.quote(str(install))} test'
    try:
        result = subprocess.run(["bash", "-c", code],
                                env={**os.environ, "ODS_MODEL_LIFECYCLE_LOCK_FD": str(fd)},
                                pass_fds=(fd,), capture_output=True, text=True, timeout=5)
        assert result.returncode != 0
        assert "does not match canonical lock" in result.stderr
        os.fstat(fd)  # A refused child cannot close the parent's descriptor.
        locks.acquire(install).close()
    finally:
        os.close(fd)
