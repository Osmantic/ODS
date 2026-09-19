"""Standalone execution of ods-doctor without lib/service-registry.sh."""

import os
import subprocess
import tempfile
from pathlib import Path


def test_doctor_tolerates_missing_service_registry():
    repo_root = Path(__file__).resolve().parents[1]
    doctor_script = repo_root / "scripts/ods-doctor.sh"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        scripts_dir = root / "scripts"
        scripts_dir.mkdir(parents=True)
        # We invoke bash with a harness that executes up through the initial port setup
        harness = f"""
set -euo pipefail
ROOT_DIR="{root}"
SCRIPT_DIR="{root}"
"""
        # Read the first 105 lines of ods-doctor.sh up to port initialization
        doctor_lines = doctor_script.read_text().splitlines()[:105]
        # Skip the ROOT_DIR recalculation from BASH_SOURCE
        filtered_lines = [
            line for line in doctor_lines
            if not line.startswith('ROOT_DIR=') and not line.startswith('SCRIPT_DIR=')
        ]
        test_script = root / "test_init.sh"
        test_script.write_text(harness + "\n" + "\n".join(filtered_lines) + """
echo "DASHBOARD_PORT=$_DASHBOARD_PORT"
echo "WEBUI_PORT=$_WEBUI_PORT"
""")

        result = subprocess.run(
            ["bash", str(test_script)],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"stdout: {result.stdout}, stderr: {result.stderr}"
        assert "DASHBOARD_PORT=3001" in result.stdout
        assert "WEBUI_PORT=3000" in result.stdout


if __name__ == "__main__":
    test_doctor_tolerates_missing_service_registry()
    print("test_doctor_standalone_registry passed.")
