"""Regression tests for macOS sandboxed Python interpreter resolution (Issue #7383).

Ensures the Operations Broker policy configures genuine Python Mach-O binaries
rather than Apple's xcrun wrapper stub (/usr/bin/python3), avoiding Seatbelt
cache denial errors and readiness timeouts during native activation.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


def resolve_macos_python():
    probe = (
        "import ctypes,os; b=ctypes.create_string_buffer(4096); "
        "n=ctypes.CDLL(None).proc_pidpath(os.getpid(),b,len(b)); "
        "assert n>0; print(b.value.decode())"
    )
    result = subprocess.run(
        ["/usr/bin/python3", "-I", "-B", "-c", probe],
        cwd="/",
        env={"PATH": "/usr/bin:/bin", "HOME": "/var/empty", "TMPDIR": "/private/tmp"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode or len(result.stdout) > 4096 or len(result.stdout.splitlines()) != 1:
        raise ValueError("native macOS Python discovery failed")
    return Path(result.stdout.strip()).resolve(strict=True)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS native Mach-O verification")
def test_macos_python_discovery_yields_regular_file():
    resolved = resolve_macos_python()
    info = resolved.lstat()
    assert resolved.is_absolute()
    assert stat.S_ISREG(info.st_mode), f"{resolved} must be a regular file"
    assert not stat.S_ISLNK(info.st_mode), f"{resolved} must not be a symlink"
    assert info.st_uid in (0, os.getuid()), f"{resolved} must be root or test-runner owned"
    assert not (info.st_mode & 0o022), f"{resolved} must not be group or world writable"
    assert os.access(resolved, os.X_OK), f"{resolved} must be executable"
    assert str(resolved) != "/usr/bin/python3", "Must not be the xcrun wrapper stub"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Seatbelt sandbox-exec execution")
def test_real_interpreter_executes_without_xcrun_cache_denial():
    real_python = resolve_macos_python()
    profile = (
        '(version 1)\n'
        '(deny default)\n'
        f'(allow process-exec (literal "{real_python}"))\n'
        '(allow file-read*)\n'
    )
    result = subprocess.run(
        ["/usr/bin/sandbox-exec", "-p", profile, str(real_python), "-c", "import sys; print('ok')"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ok"
    assert "xcrun" not in result.stderr.lower()
    assert "operation not permitted" not in result.stderr.lower()


def test_operations_policy_generation_uses_resolved_python():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        policy_dir = tmp / "etc"
        policy_dir.mkdir(mode=0o700)
        os.chmod(policy_dir, 0o700)
        policy_file = policy_dir / "policy.json"

        observer = tmp / "system_observe.py"
        observer.write_text("#!/usr/bin/env python3\n")
        observer.chmod(0o755)

        script = (
            Path(__file__).resolve().parents[1]
            / "installers/lib/pixel-host-install.sh"
        )
        assert script.is_file()

        content = script.read_text(encoding="utf-8")
        func_marker = "_ods_pixel_write_operations_policy()"
        func_start = content.index(func_marker)
        py_marker = "<<'PY'\n"
        start = content.index(py_marker, func_start) + len(py_marker)
        end = content.index("\nPY\n", start)
        embedded_py = content[start:end]

        py_file = tmp / "generate_policy.py"
        py_file.write_text(embedded_py, encoding="utf-8")

        res = subprocess.run(
            [
                sys.executable,
                str(py_file),
                str(policy_file),
                str(tmp),
                str(tmp / "workspace"),
                str(observer),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert res.returncode == 0, f"Policy generation failed: {res.stderr}"
        data = json.loads(policy_file.read_text(encoding="utf-8"))

        if sys.platform == "darwin":
            resolved = resolve_macos_python()
            os_release_argv = data["actions"]["host.os-release"]["argv"]
            if resolved.lstat().st_uid == 0:
                assert os_release_argv[0] == str(resolved)
                assert os_release_argv[0] != "/usr/bin/python3"
            else:
                assert os_release_argv[0] == str(Path("/usr/bin/python3").resolve())
        else:
            cat_binary = str(Path(shutil.which("cat")).resolve())
            assert data["actions"]["host.os-release"]["argv"] == [cat_binary, "/etc/os-release"]
            assert data["actions"]["host.gpu"]["argv"][0] == str(Path("/usr/bin/python3").resolve())
