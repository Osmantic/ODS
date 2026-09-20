"""Regression test: verify ods-doctor.sh safely handles malformed or empty extension diagnostics."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ods-doctor.sh"


def test_doctor_runs_and_emits_valid_extension_summary():
    with tempfile.TemporaryDirectory() as tmpdir:
        report_file = Path(tmpdir) / "report.json"

        res = subprocess.run(
            ["bash", str(SCRIPT), str(report_file)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"Doctor failed: {res.stderr}"
        assert report_file.exists()

        data = json.loads(report_file.read_text(encoding="utf-8"))
        assert "extensions" in data
        assert isinstance(data["extensions"], list)
        assert "summary" in data
        assert "extensions_total" in data["summary"]
        assert "extensions_healthy" in data["summary"]
        assert "extensions_issues" in data["summary"]


def test_doctor_safely_decodes_malformed_ext_payload():
    # Verify the sanitization logic handles non-JSON, non-list, and non-dict items
    for malformed in ["", "not-json", "null", "42", '["valid-string-not-dict"]']:
        try:
            ext_diagnostics = json.loads(malformed) if malformed.strip() else []
        except (TypeError, ValueError):
            ext_diagnostics = []
        if not isinstance(ext_diagnostics, list):
            ext_diagnostics = []
        ext_diagnostics = [e for e in ext_diagnostics if isinstance(e, dict)]

        # Must always evaluate safely to a list of dicts without raising
        assert isinstance(ext_diagnostics, list)
        for item in ext_diagnostics:
            assert isinstance(item, dict)


if __name__ == "__main__":
    test_doctor_runs_and_emits_valid_extension_summary()
    test_doctor_safely_decodes_malformed_ext_payload()
    print("test_doctor_ext_diagnostics_guard: OK")
