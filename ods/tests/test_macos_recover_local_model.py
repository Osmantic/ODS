#!/usr/bin/env python3
"""Regression tests for recover-macos-local-model environment and import safety."""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "scripts" / "recover-macos-local-model.py"


def _run(install_dir: Path) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--install-dir",
        str(install_dir),
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


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
        assert "Traceback" not in res.stderr


def test_missing_spec_loader_handled_without_traceback():
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


if __name__ == "__main__":
    test_import_error_handled_without_traceback()
    test_missing_load_env_attribute_handled_without_traceback()
    test_missing_spec_loader_handled_without_traceback()
    print("[PASS] All recover-macos-local-model regression tests passed.")
