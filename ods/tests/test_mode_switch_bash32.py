#!/usr/bin/env python3
"""Regression test: mode-switch.sh must run under Bash 3.2 without bad substitution."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MODE_SWITCH = ROOT / "scripts" / "mode-switch.sh"


def test_mode_switch_bash32_compatibility():
    # macOS system /bin/bash is bash 3.2; test explicitly with /bin/bash
    bash_bin = "/bin/bash" if Path("/bin/bash").exists() else "bash"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # Create a mock repo structure so mode-switch can locate .env
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir(parents=True)
        script_copy = scripts_dir / "mode-switch.sh"
        shutil.copy2(MODE_SWITCH, script_copy)
        env_file = tmp_path / ".env"

        # Case 1: Switching to cloud mode with uppercase backend should not crash
        env_file.write_text("LLM_BACKEND=\"LLAMA-SERVER\"\nODS_MODE=local\n", encoding="utf-8")
        res = subprocess.run(
            [bash_bin, str(script_copy), "cloud"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"stdout: {res.stdout}, stderr: {res.stderr}"
        assert "bad substitution" not in res.stderr
        updated = env_file.read_text(encoding="utf-8")
        assert "ODS_MODE=cloud" in updated

        # Case 2: External backend with mixed casing must be safely detected and rejected
        env_file.write_text("LLM_BACKEND=\"External\"\nODS_MODE=local\n", encoding="utf-8")
        res2 = subprocess.run(
            [bash_bin, str(script_copy), "cloud"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res2.returncode != 0
        assert "bad substitution" not in res2.stderr
        assert "External LLM routing is installer-managed" in res2.stderr


if __name__ == "__main__":
    test_mode_switch_bash32_compatibility()
    print("test_mode_switch_bash32: OK")
