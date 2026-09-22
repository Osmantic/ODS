"""Regression test: verify ods-doctor.sh isolates and cleans up temp files by PID."""

import os
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ods-doctor.sh"


def test_doctor_tempfiles_isolated_and_cleaned():
    with tempfile.TemporaryDirectory() as tmpdir:
        report_file = Path(tmpdir) / "report.json"
        env = os.environ.copy()
        env["TMPDIR"] = tmpdir

        res = subprocess.run(
            ["bash", str(SCRIPT), str(report_file)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert res.returncode == 0, f"Doctor failed: {res.stderr}"
        assert report_file.exists()

        # Verify no intermediate capability or preflight json files were leaked
        remaining = list(Path(tmpdir).glob("ods-doctor-*.json"))
        # Only the requested report.json should be present
        for f in remaining:
            assert f.name == "report.json", f"Leaked intermediate tempfile: {f.name}"


if __name__ == "__main__":
    test_doctor_tempfiles_isolated_and_cleaned()
    print("test_doctor_tempfile_isolation: OK")
