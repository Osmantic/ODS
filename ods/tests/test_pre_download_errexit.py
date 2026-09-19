#!/usr/bin/env python3
"""Regression test: pre-download.sh download_model returns 1 instead of aborting under set -e."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRE_DOWNLOAD = ROOT / "scripts" / "pre-download.sh"


def test_download_model_failure_returns_one_cleanly():
    # Simulate a failing python invocation where download_model is called with set -euo pipefail
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        mock_py = tmp_path / "mock-python.sh"
        mock_py.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        mock_py.chmod(0o755)

        test_script = tmp_path / "test-runner.sh"
        test_script.write_text(f"""#!/bin/bash
set -euo pipefail
ODS_PYTHON_CMD="{mock_py}"
source "{PRE_DOWNLOAD}"

# download_model should fail and return 1 without terminating the shell
if download_model "mock/model" "Mock Model"; then
    echo "UNEXPECTED_SUCCESS"
    exit 2
else
    echo "CAUGHT_FAILURE"
fi
exit 0
""", encoding="utf-8")
        test_script.chmod(0o755)

        res = subprocess.run(
            ["bash", str(test_script)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"stdout: {res.stdout}, stderr: {res.stderr}"
        assert "CAUGHT_FAILURE" in res.stdout
        assert "Failed to download Mock Model" in res.stderr or "Failed to download Mock Model" in res.stdout


if __name__ == "__main__":
    test_download_model_failure_returns_one_cleanly()
    print("test_pre_download_errexit: OK")
