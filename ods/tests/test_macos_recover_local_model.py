#!/usr/bin/env python3
"""Regression tests for recover-macos-local-model environment and import safety."""

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "scripts" / "recover-macos-local-model.py"


def _run(install_dir: Path) -> subprocess.CompletedProcess[str]:
    # Linux CI must reach the installed-module boundary rather than passing
    # accidentally at the earlier Darwin/owner guard. Only platform identity is
    # substituted; the real CLI runs against this disposable installation.
    entrypoint = (
        "import os, platform, runpy, sys, urllib.request; "
        "from unittest import mock; "
        "platform.system = lambda: 'Darwin'; os.geteuid = lambda: 501; "
        "urllib.request.build_opener = mock.Mock(side_effect=AssertionError('network forbidden')); "
        "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
    )
    cmd = [
        sys.executable,
        "-B", "-c", entrypoint,
        str(SCRIPT),
        "--install-dir",
        str(install_dir),
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=10)


def test_import_error_handled_without_traceback():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        bin_dir = tdp / "bin"
        bin_dir.mkdir()
        agent = bin_dir / "ods-host-agent.py"
        agent.write_text('raise ImportError("missing model_memory")\n', encoding="utf-8")
        env = tdp / ".env"
        env.write_text("GPU_BACKEND=apple\nODS_MODE=local\nODS_AGENT_KEY=test\n", encoding="utf-8")

        res = _run(tdp)
        assert res.returncode == 1, res.stderr
        assert "Recovery stopped" in res.stderr
        assert "Recovery stopped (ImportError)" in res.stderr
        assert "Traceback" not in res.stderr


def test_missing_load_env_attribute_handled_without_traceback():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        bin_dir = tdp / "bin"
        bin_dir.mkdir()
        agent = bin_dir / "ods-host-agent.py"
        agent.write_text("X = 1\n", encoding="utf-8")
        env = tdp / ".env"
        env.write_text("GPU_BACKEND=apple\nODS_MODE=local\nODS_AGENT_KEY=test\n", encoding="utf-8")

        res = _run(tdp)
        assert res.returncode == 1, res.stderr
        assert "Recovery stopped" in res.stderr
        assert "The installed host agent cannot load environment configuration" in res.stderr
        assert "Traceback" not in res.stderr


def test_directory_at_agent_path_handled_without_traceback():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        bin_dir = tdp / "bin"
        bin_dir.mkdir()
        # Invalid directory at agent path
        (bin_dir / "ods-host-agent.py").mkdir()

        res = _run(tdp)
        assert res.returncode == 1, res.stderr
        assert "Recovery stopped" in res.stderr
        assert "Traceback" not in res.stderr


def test_invalid_loader_specs_are_rejected_without_loading():
    spec = importlib.util.spec_from_file_location("recovery_test", SCRIPT)
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    for invalid in (None, importlib.util.spec_from_loader("missing", loader=None)):
        with mock.patch.object(recovery.importlib.util, "spec_from_file_location", return_value=invalid):
            try:
                recovery.installed_env(Path("/not-an-install"))
            except recovery.RecoveryError as error:
                assert str(error) == "The installed host agent is missing or invalid"
            else:
                raise AssertionError("Invalid module loader was accepted")


def test_broken_environment_exports_are_safely_reported():
    # Run the actual public CLI: neither source lines nor exception values may
    # leak from a damaged module, and no recovery request should be attempted.
    sources = (
        "load_env = None\n",
        "load_env = 'private-test-sentinel'\n",
        "private-test-sentinel invalid syntax\n",
        "def load_env(path):\n    raise TypeError('private-test-sentinel')\n",
        "def load_env(path):\n    raise AttributeError('private-test-sentinel')\n",
        "raise ModuleNotFoundError('private-test-sentinel')\n",
    )
    with tempfile.TemporaryDirectory() as td:
        install = Path(td)
        (install / "bin").mkdir()
        for source in sources:
            (install / "bin/ods-host-agent.py").write_text(source, encoding="utf-8")
            result = _run(install)
            assert result.returncode == 1, result.stderr
            assert "Recovery stopped" in result.stderr
            assert "Keep all saved state" in result.stderr
            assert "Traceback" not in result.stderr
            assert "private-test-sentinel" not in result.stdout + result.stderr
            assert not result.stdout


if __name__ == "__main__":
    test_import_error_handled_without_traceback()
    test_missing_load_env_attribute_handled_without_traceback()
    test_directory_at_agent_path_handled_without_traceback()
    test_invalid_loader_specs_are_rejected_without_loading()
    test_broken_environment_exports_are_safely_reported()
    print("[PASS] All recover-macos-local-model regression tests passed.")
