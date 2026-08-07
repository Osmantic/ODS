#!/usr/bin/env python3
"""CLI coverage for golden-path JSON reports."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-golden-paths.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def main() -> int:
    valid = run("--json")
    valid_report = json.loads(valid.stdout)
    assert valid.returncode == 0 and valid_report["ok"] is True
    assert valid_report["errors"] == []

    with tempfile.TemporaryDirectory() as temp_dir:
        invalid_path = Path(temp_dir) / "golden.json"
        invalid_path.write_text("[]\n", encoding="utf-8")
        invalid = run(str(invalid_path), "--json")
        invalid_report = json.loads(invalid.stdout)
        assert invalid.returncode == 1 and invalid_report["ok"] is False
        assert invalid_report["errors"] == ["$: must be an object"]

    print("[PASS] golden-path JSON report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
