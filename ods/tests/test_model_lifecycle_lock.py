import os
import sys
import stat
import select
import subprocess
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux only")

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "bin" / "model_lifecycle_lock.py"


def _load():
    spec = importlib.util.spec_from_file_location("model_lifecycle_lock", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mll(monkeypatch, tmp_path):
    root = tmp_path / "locks"
    monkeypatch.setenv("ODS_MODEL_LIFECYCLE_LOCK_ROOT", str(root))
    monkeypatch.delenv("INSTALL_USER", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    install = tmp_path / "install"
    install.mkdir()
    (install / "model.bin").write_bytes(b"hello-model")
    mod = _load()
    mod._test_install = install
    return mod


def _lockfile(mll, install):
    return mll.lock_path(install)


def _child_env(mll):
    env = dict(os.environ)
    env["ODS_MODEL_LIFECYCLE_LOCK_ROOT"] = os.environ["ODS_MODEL_LIFECYCLE_LOCK_ROOT"]
    return env


def _probe(mll, install, timeout=5):
    code = (
        "import sys, importlib.util\n"
        f"spec=importlib.util.spec_from_file_location('m',{str(MOD_PATH)!r})\n"
        "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        f"p={str(install)!r}\n"
        "try:\n"
        "    lease=m.acquire(__import__('pathlib').Path(p), blocking=False)\n"
        "    lease.close()\n"
        "except BlockingIOError:\n"
        "    sys.exit(2)\n"
        "except Exception:\n"
        "    sys.exit(3)\n"
        "sys.exit(0)\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        env=_child_env(mll), timeout=timeout, capture_output=True,
    )


def _holder(mll, install, pass_fds=()):
    code = (
        "import sys, importlib.util\n"
        f"spec=importlib.util.spec_from_file_location('m',{str(MOD_PATH)!r})\n"
        "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        + (f"m.validate_fd(__import__('pathlib').Path({str(install)!r}), {pass_fds[0]})\n"
           if pass_fds else "")
        +
        "print('READY', flush=True)\n"
        "sys.stdin.readline()\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code],
        env=_child_env(mll), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        pass_fds=pass_fds,
    )


def _wait_ready(proc, timeout=5):
    r, _, _ = select.select([proc.stdout], [], [], timeout)
    assert r, "child did not become ready"
    line = proc.stdout.readline()
    assert line.strip() == b"READY"


def _stop(proc):
    try:
        if proc.stdin:
            proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def test_sigkill_parent_preserves_child_lease(mll, tmp_path):
    import signal
    import time

    ready, stop, pid_file = (tmp_path / name for name in ("ready", "stop", "pid"))
    child_code = (
        "import pathlib,sys,time; "
        f"sys.path.insert(0, {str(MOD_PATH.parent)!r}); "
        "import model_lifecycle_lock as m; "
        f"m.validate_fd(pathlib.Path({str(mll._test_install)!r}), int(sys.argv[1])); "
        f"pathlib.Path({str(ready)!r}).touch()\n"
        f"while not pathlib.Path({str(stop)!r}).exists(): time.sleep(.02)\n"
    )
    parent_code = (
        "import pathlib,sys,time,subprocess; "
        f"sys.path.insert(0, {str(MOD_PATH.parent)!r}); "
        "import model_lifecycle_lock as m; "
        f"lease=m.acquire(pathlib.Path({str(mll._test_install)!r})); "
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r},str(lease.fd)], "
        "pass_fds=(lease.fd,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid)); time.sleep(30)"
    )
    parent = subprocess.Popen([sys.executable, "-c", parent_code], stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while not (ready.exists() and pid_file.exists()):
            assert parent.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.02)
        parent.kill()  # Actual owner process death, not a normal lease.close().
        parent.wait(timeout=5)
        assert _probe(mll, mll._test_install).returncode == 2
        stop.touch()
        deadline = time.monotonic() + 5
        while _probe(mll, mll._test_install).returncode == 2:
            assert time.monotonic() < deadline
            time.sleep(.02)
        assert _probe(mll, mll._test_install).returncode == 0
    finally:
        stop.touch()
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
        if parent.stderr:
            parent.stderr.close()


def test_lock_path_pure(mll):
    p = mll.lock_path(mll._test_install)
    assert p == mll.lock_path(mll._test_install)
    assert p.parent == Path(os.environ["ODS_MODEL_LIFECYCLE_LOCK_ROOT"])
    assert not p.parent.exists()
    checksum = subprocess.run(["cksum"], input=os.fsencode(mll._test_install.resolve()) + b"\0",
                              capture_output=True, check=True, timeout=5).stdout.split()[0].decode()
    assert p.name == f"ods-model-lifecycle-{checksum}.lock"


def test_prepare_creates_private(mll):
    p = mll.prepare_lock_path(mll._test_install)
    assert p.exists()
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_acquire_idempotent(mll):
    p = mll.prepare_lock_path(mll._test_install)
    ino = p.stat().st_ino
    content = p.read_bytes()
    l1 = mll.acquire(mll._test_install, blocking=False)
    l1.close()
    l2 = mll.acquire(mll._test_install, blocking=False)
    l2.close()
    assert p.stat().st_ino == ino
    assert p.read_bytes() == content
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_canonical_alias(mll, tmp_path):
    real = mll._test_install
    alias = tmp_path / "alias"
    alias.symlink_to(real)
    assert mll.lock_path(alias) == mll.lock_path(real)


def test_competing_blocked(mll):
    lease = mll.acquire(mll._test_install, blocking=False)
    try:
        r = _probe(mll, mll._test_install)
        assert r.returncode == 2, r.stderr
    finally:
        lease.close()
    r = _probe(mll, mll._test_install)
    assert r.returncode == 0, r.stderr


def test_passed_fd_child_holds(mll):
    lease = mll.acquire(mll._test_install, blocking=False)
    proc = _holder(mll, mll._test_install, pass_fds=(lease.fd,))
    try:
        _wait_ready(proc)
        lease.close()
        r = _probe(mll, mll._test_install)
        assert r.returncode == 2, r.stderr
    finally:
        lease.close()
        _stop(proc)
    r = _probe(mll, mll._test_install)
    assert r.returncode == 0, r.stderr


def test_child_without_pass_fds(mll):
    lease = mll.acquire(mll._test_install, blocking=False)
    proc = _holder(mll, mll._test_install)
    try:
        _wait_ready(proc)
        lease.close()
        r = _probe(mll, mll._test_install)
        assert r.returncode == 0, r.stderr
    finally:
        lease.close()
        _stop(proc)


def test_legacy_644_tightened(mll):
    p = mll.prepare_lock_path(mll._test_install)
    p.chmod(0o644)
    ino = p.stat().st_ino
    lease = mll.acquire(mll._test_install, blocking=False)
    lease.close()
    assert p.stat().st_ino == ino
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


@pytest.mark.parametrize("mode", [0o666, 0o640, 0o604, 0o700])
def test_bad_modes_refused(mll, mode):
    p = mll.prepare_lock_path(mll._test_install)
    p.chmod(mode)
    with pytest.raises(RuntimeError):
        mll.acquire(mll._test_install, blocking=False)
    assert stat.S_IMODE(p.stat().st_mode) == mode


def test_symlink_refused(mll, tmp_path):
    p = mll.prepare_lock_path(mll._test_install)
    victim = tmp_path / "victim"
    victim.write_bytes(b"x")
    p.unlink()
    p.symlink_to(victim)
    with pytest.raises(RuntimeError):
        mll.acquire(mll._test_install, blocking=False)
    assert victim.read_bytes() == b"x"


def test_hardlink_refused(mll, tmp_path):
    p = mll.prepare_lock_path(mll._test_install)
    victim = tmp_path / "victim"
    victim.write_bytes(b"x")
    p.unlink()
    os.link(victim, p)
    with pytest.raises(RuntimeError):
        mll.acquire(mll._test_install, blocking=False)
    assert victim.read_bytes() == b"x"


def test_fifo_refused(mll):
    p = mll.prepare_lock_path(mll._test_install)
    p.unlink()
    os.mkfifo(p, 0o600)
    with pytest.raises(RuntimeError):
        mll.acquire(mll._test_install, blocking=False)


def test_validate_fd_mismatch(mll):
    lease = mll.acquire(mll._test_install, blocking=False)
    try:
        other = mll._test_install.parent / "other"
        other.mkdir()
        (other / "model.bin").write_bytes(b"other")
        with pytest.raises(RuntimeError):
            mll.validate_fd(other, lease.fd)
    finally:
        lease.close()


def test_validate_fd_refuses_stdin(mll):
    with pytest.raises(RuntimeError):
        mll.validate_fd(mll._test_install, 0)


def test_existing_unlocked_acquirable(mll):
    p = mll.prepare_lock_path(mll._test_install)
    ino = p.stat().st_ino
    lease = mll.acquire(mll._test_install, blocking=False)
    lease.close()
    assert p.exists()
    assert p.stat().st_ino == ino


@pytest.mark.parametrize("kind", ["symlink", "mode"])
def test_unsafe_root_refused(mll, tmp_path, kind):
    root = Path(os.environ["ODS_MODEL_LIFECYCLE_LOCK_ROOT"])
    if kind == "symlink":
        victim = tmp_path / "outside"
        victim.mkdir(mode=0o700)
        root.symlink_to(victim, target_is_directory=True)
    else:
        root.mkdir(mode=0o700)
        root.chmod(0o777)
    with pytest.raises(RuntimeError):
        mll.prepare_lock_path(mll._test_install)
    assert list(root.iterdir()) == []
    if kind == "mode":
        assert stat.S_IMODE(root.stat().st_mode) == 0o777


@pytest.mark.parametrize("payload", [
    b"", b"hello world", b"\x00\x01\x02\x03", "héllo wörld".encode(), bytes(range(256)),
])
def test_native_cksum_matches_posix_vectors(mll, payload):
    expected = subprocess.run(["cksum"], input=payload, capture_output=True,
                              check=True, timeout=5).stdout.split()[0].decode()
    assert mll._cksum(payload) == expected


@pytest.mark.parametrize("length", [1, 2, 31, 255, 256, 257, 1024, 4096])
def test_native_cksum_matches_posix_length_boundaries(mll, length):
    import random
    rng = random.Random(length)
    payload = bytes(rng.getrandbits(8) for _ in range(length))
    expected = subprocess.run(["cksum"], input=payload, capture_output=True,
                              check=True, timeout=5).stdout.split()[0].decode()
    assert mll._cksum(payload) == expected
