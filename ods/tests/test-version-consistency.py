#!/usr/bin/env python3
"""Public CLI coverage for version consistency JSON output."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-version-consistency.py"


def main() -> int:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0, report
    assert report["ok"] is True
    assert report["expected"]
    assert report["errors"] == []
    print("[PASS] version consistency JSON report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
